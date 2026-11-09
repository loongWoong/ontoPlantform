"""Per-(event, webhook) delivery ledger for outbound webhooks (P2.2-B).

``WebhookService`` used to record a webhook attempt only as aggregate counters
on the subscription row (``delivery_count`` / ``last_error``) plus a single
``webhook_delivered_at`` marker on the outbox event.  That is enough for one
in-process worker but cannot express *which* (event, webhook) pairs are still
owed, how many attempts each pair consumed, or which worker currently holds a
delivery.  This module adds the missing unit of work: one row per
``(event, webhook)`` pair, addressed by a deterministic id so repeated dispatch
passes are idempotent.

Row layout (kind ``webhook_delivery``)::

    id, workspace_id, webhook_id, event_id, event_doc_id, event_type,
    status (pending|delivered|failed|dead), attempts, lease_expires_at,
    lease_owner, next_attempt_at, last_status_code, last_error,
    response_excerpt, created_at, updated_at

``event_doc_id`` is the repository key of the source outbox row; it lets a
replayed delivery clear the event's terminal ``webhook_delivered_at`` marker
without a full scan.

Concurrency
-----------
Every state transition is a compare-and-set on the repository's document
version (``get_versioned`` + ``put_if_version``): two processes can race a
claim, only one wins, and an expired lease may be taken over.  No new runtime
dependency is used.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from ...errors import ConcurrencyConflictError

WEBHOOK_DELIVERY_KIND = "webhook_delivery"

#: Default lease for a claimed delivery.  A worker that dies mid-delivery lets
#: another one retry after this many seconds.
DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_BACKOFF_BASE_SECONDS = 5.0
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RATE_LIMIT_PER_MINUTE = 60

DELIVERY_STATUSES = ("pending", "delivered", "failed", "dead")
TERMINAL_STATUSES = ("delivered", "dead")
RETRIABLE_STATUSES = ("pending", "failed")

#: CAS retries before a race loser gives up for this dispatch pass.
CAS_RETRIES = 8

Clock = Callable[[], str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp (tolerant: bad/absent values become ``None``)."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def delivery_id_for(event_id: str, webhook_id: str) -> str:
    """Stable id of one (event, webhook) delivery row."""
    digest = hashlib.sha256(
        f"{event_id}|{webhook_id}".encode("utf-8")).hexdigest()
    return f"whd_{digest}"


def backoff_seconds(attempts: int,
                    base: float = DEFAULT_BACKOFF_BASE_SECONDS) -> float:
    """Exponential retry delay for the given number of failed attempts."""
    base = max(1.0, float(base))
    return base * float(2 ** max(0, int(attempts) - 1))


def is_retry_due(row: Mapping[str, Any], now: datetime) -> bool:
    """Whether a delivery may be attempted now.

    ``failed`` rows wait for ``next_attempt_at``; the *first* retry stays
    immediate for backwards compatibility with the single-worker dispatcher
    (existing tests and operators rely on calling ``dispatch_webhooks()``
    twice in a row to retry).  Every later retry honours the exponential
    backoff written by ``finish``.
    """
    status = str(row.get("status") or "pending")
    if status in TERMINAL_STATUSES:
        return False
    if status == "pending":
        return True
    if status != "failed":
        return False
    attempts = int(row.get("attempts") or 0)
    if attempts <= 1:
        return True
    next_attempt = parse_ts(row.get("next_attempt_at"))
    return next_attempt is None or next_attempt <= now


def new_delivery_row(workspace_id: str, webhook_id: str,
                     event: Mapping[str, Any], now: str) -> dict:
    """Build a fresh ``pending`` delivery row for one (event, webhook) pair."""
    event_id = str(event.get("event_id") or event.get("id") or "")
    event_doc_id = str(event.get("id") or event_id)
    return {
        "id": delivery_id_for(event_id, webhook_id),
        "workspace_id": workspace_id,
        "webhook_id": webhook_id,
        "event_id": event_id,
        "event_doc_id": event_doc_id,
        "event_type": str(event.get("type") or ""),
        "status": "pending",
        "attempts": 0,
        "lease_expires_at": None,
        "lease_owner": None,
        "next_attempt_at": None,
        "last_status_code": None,
        "last_error": None,
        "response_excerpt": None,
        "created_at": now,
        "updated_at": now,
    }


class WebhookDeliveryLedger:
    """Version-guarded storage for ``webhook_delivery`` rows.

    The ledger owns only row-level state transitions; authorization, quota and
    event-marker bookkeeping stay in ``WebhookService``.
    """

    def __init__(self, ctx: Any, clock: Clock | None = None) -> None:
        self.ctx = ctx
        self._clock: Clock = clock or _now

    # ---- storage helpers --------------------------------------------------

    def _now(self) -> str:
        clock = self._clock
        return clock() if callable(clock) else _now()

    def row_id(self, event_id: str, webhook_id: str) -> str:
        return delivery_id_for(event_id, webhook_id)

    def get(self, workspace_id: str, delivery_id: str) -> dict | None:
        row = self.ctx.repo.get(WEBHOOK_DELIVERY_KIND, workspace_id,
                                delivery_id)
        return deepcopy(dict(row)) if row is not None else None

    def get_or_create(self, workspace_id: str, webhook_id: str,
                      event: Mapping[str, Any]) -> dict:
        """Return the delivery row, creating it exactly once.

        ``put_if_version(expected_version=0)`` turns a concurrent create into a
        ``ConcurrencyConflictError``; the loser re-reads the winner's row.
        """
        event_id = str(event.get("event_id") or event.get("id") or "")
        delivery_id = self.row_id(event_id, webhook_id)
        existing = self.get(workspace_id, delivery_id)
        if existing is not None:
            return existing
        now = self._now()
        row = new_delivery_row(workspace_id, webhook_id, event, now)
        try:
            self.ctx.repo.put_if_version(
                WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id, row, 0)
        except ConcurrencyConflictError:
            raced = self.get(workspace_id, delivery_id)
            if raced is not None:
                return raced
        return row

    # ---- state transitions ------------------------------------------------

    def claim(self, workspace_id: str, delivery_id: str, owner: str,
              lease_seconds: float = DEFAULT_LEASE_SECONDS) -> dict | None:
        """CAS-claim a due delivery for ``owner``; ``None`` when not claimable.

        A delivery is claimable while it is ``pending``/``failed``, its retry
        time has arrived, and no other worker holds an unexpired lease.  An
        expired lease is taken over by the next claimant.
        """
        now_dt = parse_ts(self._now()) or datetime.now(timezone.utc)
        lease_seconds = max(1.0, float(lease_seconds))
        for _ in range(CAS_RETRIES):
            got = self.ctx.repo.get_versioned(
                WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id)
            if got is None:
                return None
            row, version = got
            row = dict(row)
            if row.get("status") not in RETRIABLE_STATUSES:
                return None
            if not is_retry_due(row, now_dt):
                return None
            lease = parse_ts(row.get("lease_expires_at"))
            if lease is not None and lease > now_dt:
                return None
            claimed = deepcopy(row)
            claimed["lease_owner"] = owner
            claimed["lease_expires_at"] = iso(
                now_dt + timedelta(seconds=lease_seconds))
            claimed["updated_at"] = iso(now_dt)
            try:
                self.ctx.repo.put_if_version(
                    WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id,
                    claimed, version)
            except ConcurrencyConflictError:
                continue
            return claimed
        return None

    def finish(self, workspace_id: str, delivery_id: str, *, delivered: bool,
               status_code: int | None = None, error: str | None = None,
               response_excerpt: str | None = None,
               max_attempts: int = DEFAULT_MAX_ATTEMPTS,
               backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
               ) -> dict | None:
        """Persist the outcome of one attempt and clear the lease.

        Failures below ``max_attempts`` become ``failed`` with an exponential
        ``next_attempt_at``; reaching the limit moves the row to ``dead``.
        Terminal rows are never rewritten (idempotent completion).
        """
        now_dt = parse_ts(self._now()) or datetime.now(timezone.utc)
        now = iso(now_dt)
        updated: dict | None = None
        for _ in range(CAS_RETRIES):
            got = self.ctx.repo.get_versioned(
                WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id)
            if got is None:
                return None
            row, version = got
            row = dict(row)
            if row.get("status") in TERMINAL_STATUSES:
                return deepcopy(row)
            attempts = int(row.get("attempts") or 0) + 1
            updated = deepcopy(row)
            updated["attempts"] = attempts
            updated["lease_expires_at"] = None
            updated["lease_owner"] = None
            updated["updated_at"] = now
            updated["last_status_code"] = (
                int(status_code) if status_code is not None else None)
            if delivered:
                updated["status"] = "delivered"
                updated["next_attempt_at"] = None
                updated["last_error"] = None
                updated["response_excerpt"] = (response_excerpt or "")[:500]
            else:
                updated["last_error"] = (error or "")[:500]
                updated["response_excerpt"] = (response_excerpt or "")[:500]
                if attempts >= int(max_attempts):
                    updated["status"] = "dead"
                    updated["next_attempt_at"] = None
                else:
                    updated["status"] = "failed"
                    updated["next_attempt_at"] = iso(
                        now_dt + timedelta(
                            seconds=backoff_seconds(
                                attempts, backoff_base_seconds)))
            try:
                self.ctx.repo.put_if_version(
                    WEBHOOK_DELIVERY_KIND, workspace_id, delivery_id,
                    updated, version)
            except ConcurrencyConflictError:
                continue
            return updated
        return updated

    # ---- queries ----------------------------------------------------------

    def list(self, workspace_id: str, webhook_id: str | None = None,
             status: str | None = None, limit: int = 100) -> list[dict]:
        """List delivery rows newest-first, optionally filtered."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 100
        rows = [deepcopy(dict(row)) for row in
                self.ctx.repo.list(WEBHOOK_DELIVERY_KIND, workspace_id)]
        if webhook_id:
            rows = [row for row in rows if row.get("webhook_id") == webhook_id]
        if status is not None:
            normalized = str(status).strip().lower()
            rows = [row for row in rows if row.get("status") == normalized]
        rows.sort(key=lambda row: (str(row.get("created_at") or ""),
                                   str(row.get("id") or "")))
        rows.reverse()
        return rows[:max(0, limit)]

    def dead(self, workspace_id: str, limit: int = 100) -> list[dict]:
        return self.list(workspace_id, status="dead", limit=limit)


__all__ = [
    "CAS_RETRIES",
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RATE_LIMIT_PER_MINUTE",
    "DELIVERY_STATUSES",
    "RETRIABLE_STATUSES",
    "TERMINAL_STATUSES",
    "WEBHOOK_DELIVERY_KIND",
    "WebhookDeliveryLedger",
    "backoff_seconds",
    "delivery_id_for",
    "iso",
    "is_retry_due",
    "new_delivery_row",
    "parse_ts",
]
