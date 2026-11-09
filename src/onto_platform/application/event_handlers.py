"""Persistent event handlers + per-(event, handler) delivery ledger (P2.2).

P1/P2-B left ``handler_key`` as a process-local dictionary: a key registered in
worker A was invisible to worker B, so a subscription could only ever be served
by one process, and a pending event had no per-consumer state to resume from.
This module adds the two durable documents that make dispatch safe across
workers sharing one database - no broker, no new dependency:

* ``event_handler`` - a workspace-scoped registry row per ``handler_key``:
  which event types it serves, whether it is active, how it is executed
  (``in_process`` callback or signed ``http`` POST), where it lives
  (``last_heartbeat_at``) and the lease length used by claimers.
* ``event_delivery`` - one row per ``(event, handler_key)`` pair, keyed by
  ``sha256(event_id|handler_key)``.  ``pending -> delivered`` (or ``failed`` /
  ``dead``) transitions use ``get_versioned`` + ``put_if_version`` compare-and-
  set with a lease, so two workers racing for the same pair: exactly one runs
  it.  A crashed worker's lease expires and another may take over; delivered
  and dead rows are terminal and never execute again.

The outbox itself (``EventLog``) is unchanged: an event is only stamped
``dispatched_at`` when *every* delivery row for it is terminal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from typing import Callable, Iterable

from ..domain.event.model import validate_event_name
from ..errors import (
    ConcurrencyConflictError,
    NotFoundError,
    ValidationError,
)
from .common import ApplicationContext

#: How a registered handler is executed.
HANDLER_KINDS: tuple[str, ...] = ("in_process", "http")

#: Delivery terminal states: they never execute again.
DELIVERY_TERMINAL: tuple[str, ...] = ("delivered", "dead")

#: Retry budget before a delivery is parked in ``dead`` (mirrors the outbox's
#: ``events.MAX_DELIVERY_ATTEMPTS``; kept local to avoid an import cycle).
DELIVERY_MAX_ATTEMPTS = 5

#: A ``handler_key`` is a stable operational name, not a free-form comment.
_HANDLER_KEY_MAX = 128

#: What an injected transport receives for one http handler delivery.
#: Default implementation is :func:`send_http_handler`.
Transport = Callable[[dict], None]


# ----------------------------------------------------------------------
# small time helpers (duplicated from ``events`` on purpose: this module is
# imported by ``events`` itself, so importing back would be a cycle)
# ----------------------------------------------------------------------


def _lease_expiry(now: str, lease_seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    try:
        base = datetime.fromisoformat(now)
    except ValueError:  # pragma: no cover - clock contract
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=lease_seconds)).isoformat(
        timespec="seconds")


def _next_attempt(attempts: int, now: str) -> str:
    from datetime import datetime, timedelta, timezone

    delay = 5 * (2 ** max(0, attempts - 1))
    try:
        base = datetime.fromisoformat(now)
    except ValueError:  # pragma: no cover - clock contract
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _leased(row: dict, now: str) -> bool:
    """True while a live claim lease is held on ``row``."""
    from datetime import datetime, timezone

    if not row.get("claimed_by"):
        return False
    value = row.get("lease_expires_at")
    if not value:
        return False
    try:
        expiry = datetime.fromisoformat(value)
        clock = datetime.fromisoformat(now)
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return expiry > clock


def _due(row: dict, now: str) -> bool:
    from datetime import datetime, timezone

    value = row.get("next_attempt_at")
    if not value:
        return True
    try:
        due = datetime.fromisoformat(value)
        clock = datetime.fromisoformat(now)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return due <= clock


# ----------------------------------------------------------------------
# validation helpers
# ----------------------------------------------------------------------


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _valid_handler_key(value: object) -> str:
    key = _text(value, "handler_key")
    if len(key) > _HANDLER_KEY_MAX:
        raise ValidationError(
            f"handler_key must be at most {_HANDLER_KEY_MAX} characters")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise ValidationError("handler_key must not contain whitespace or "
                              "control characters")
    return key


def _valid_event_types(value: object) -> list[str]:
    if value is None:
        raise ValidationError("event_types must be a non-empty list")
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValidationError("event_types must be a non-empty list")
    names: list[str] = []
    for raw in value:
        name = _text(raw, "event_types entry")
        validate_event_name(name)
        if name not in names:
            names.append(name)
    if not names:
        raise ValidationError("event_types must be a non-empty list")
    return names


def _valid_lease_seconds(value: object, label: str = "lease_seconds") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{label} must be an integer")
    if value < 1 or value > 3600:
        raise ValidationError(
            f"{label} must be between 1 and 3600, got {value}")
    return value


def delivery_id(event_id: str, handler_key: str) -> str:
    """Stable per-(event, handler) delivery id: ``sha256(event_id|handler_key)``."""
    digest = hashlib.sha256(
        f"{event_id}|{handler_key}".encode("utf-8")).hexdigest()
    return digest


def send_http_handler(request: dict) -> None:
    """Default http transport: signed POST of the event envelope.

    ``request`` carries ``url``/``secret``/``handler_key``/``event``.  The body
    is JSON and ``X-Onto-Event-Signature`` is ``sha256=<hmac(secret, body)>``;
    any non-2xx status raises so the delivery is retried like any failure.
    """
    body = json.dumps({
        "handler_key": request["handler_key"],
        "event": request["event"],
    }, sort_keys=True).encode("utf-8")
    signature = hmac.new(
        str(request["secret"]).encode("utf-8"), body,
        hashlib.sha256).hexdigest()
    http_request = urllib.request.Request(
        str(request["url"]), data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Onto-Event-Signature": f"sha256={signature}",
        })
    with urllib.request.urlopen(http_request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(
                f"http handler returned status {response.status}")


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------


class HandlerRegistry:
    """Workspace-scoped, persistent ``event_handler`` documents."""

    KIND = "event_handler"

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def _require_workspace(self, workspace_id: str) -> str:
        wid = _text(workspace_id, "workspace_id")
        if self._ctx.repo.get("workspace", None, wid) is None:
            raise NotFoundError(f"workspace not found: {wid!r}")
        return wid

    def get(self, workspace_id: str, handler_key: str) -> dict | None:
        wid = self._require_workspace(workspace_id)
        key = _valid_handler_key(handler_key)
        return next((row for row in self._ctx.repo.list(self.KIND, wid)
                     if row.get("handler_key") == key), None)

    def list(self, workspace_id: str) -> list[dict]:
        wid = self._require_workspace(workspace_id)
        return sorted(self._ctx.repo.list(self.KIND, wid),
                      key=lambda row: str(row.get("handler_key") or ""))

    def register(self, workspace_id: str, handler_key: str,
                 event_types: Iterable[str], *, actor: str = "system",
                 kind: str = "in_process", endpoint: str = "",
                 secret: str = "", active: bool = True,
                 lease_seconds: int = 30) -> dict:
        """Create or update the row for ``handler_key`` (never duplicate it).

        Re-registering the same key updates the existing document (version
        bumped, ``created_at``/``created_by`` preserved), which is what a
        restarting worker should call instead of inserting a second row.
        """
        wid = self._require_workspace(workspace_id)
        key = _valid_handler_key(handler_key)
        names = _valid_event_types(event_types)
        if kind not in HANDLER_KINDS:
            raise ValidationError(
                f"event handler kind must be one of {list(HANDLER_KINDS)}, "
                f"got {kind!r}")
        if not isinstance(active, bool):
            raise ValidationError("event handler active must be a boolean")
        lease_seconds = _valid_lease_seconds(lease_seconds)
        endpoint = "" if endpoint is None else str(endpoint).strip()
        secret = "" if secret is None else str(secret).strip()
        if kind == "http":
            if not endpoint.startswith(("http://", "https://")):
                raise ValidationError(
                    "http event handler needs an http(s) endpoint")
            if not secret:
                raise ValidationError("http event handler needs a secret")
        else:
            # in_process handlers have no remote address; do not keep secrets
            endpoint, secret = "", ""
        now = self._ctx.clock()
        existing = self.get(wid, key)
        if existing is not None:
            row = dict(existing)
            row.update({
                "event_types": names,
                "kind": kind,
                "endpoint": endpoint,
                "secret": secret,
                "active": active,
                "lease_seconds": lease_seconds,
                "version": int(row.get("version") or 1) + 1,
                "last_heartbeat_at": now,
                "updated_at": now,
            })
            if actor and actor != "system" and row.get("created_by") == "system":
                row["created_by"] = str(actor)
            self._ctx.repo.put(self.KIND, wid, row["id"], row)
            return row
        row = {
            "id": f"evh_{self._ctx.id_factory()}",
            "handler_key": key,
            "workspace_id": wid,
            "event_types": names,
            "kind": kind,
            "endpoint": endpoint,
            "secret": secret,
            "active": active,
            "version": 1,
            "last_heartbeat_at": now,
            "lease_seconds": lease_seconds,
            "created_by": str(actor or "system"),
            "created_at": now,
            "updated_at": now,
        }
        self._ctx.repo.put(self.KIND, wid, row["id"], row)
        return row

    def pause(self, workspace_id: str, handler_key: str) -> dict:
        return self._set_active(workspace_id, handler_key, False)

    def activate(self, workspace_id: str, handler_key: str) -> dict:
        return self._set_active(workspace_id, handler_key, True)

    def _set_active(self, workspace_id: str, handler_key: str,
                    active: bool) -> dict:
        wid = self._require_workspace(workspace_id)
        key = _valid_handler_key(handler_key)
        row = self.get(wid, key)
        if row is None:
            raise NotFoundError(f"event handler not found: {key!r}")
        updated = dict(row)
        updated["active"] = active
        updated["version"] = int(row.get("version") or 1) + 1
        updated["updated_at"] = self._ctx.clock()
        self._ctx.repo.put(self.KIND, wid, row["id"], updated)
        return updated

    def heartbeat(self, workspace_id: str, handler_key: str,
                  worker_id: str = "") -> dict:
        """Refresh liveness for a key (called by the owning worker)."""
        wid = self._require_workspace(workspace_id)
        key = _valid_handler_key(handler_key)
        row = self.get(wid, key)
        if row is None:
            raise NotFoundError(f"event handler not found: {key!r}")
        now = self._ctx.clock()
        updated = dict(row)
        updated["last_heartbeat_at"] = now
        updated["version"] = int(row.get("version") or 1) + 1
        updated["updated_at"] = now
        if worker_id:
            updated["last_worker"] = str(worker_id)
        self._ctx.repo.put(self.KIND, wid, row["id"], updated)
        return updated

    def resolve(self, event: dict) -> list[dict]:
        """Active handlers registered for ``event``'s type, in key order."""
        if not isinstance(event, dict):
            return []
        wid = event.get("workspace_id")
        event_type = event.get("type")
        if not isinstance(wid, str) or not wid or not isinstance(event_type, str):
            return []
        rows = []
        for row in self._ctx.repo.list(self.KIND, wid):
            types = row.get("event_types") or []
            if row.get("active") and event_type in types:
                rows.append(row)
        return sorted(rows, key=lambda row: str(row.get("handler_key") or ""))


# ----------------------------------------------------------------------
# delivery ledger
# ----------------------------------------------------------------------


class DeliveryTracker:
    """Per-(event, handler) delivery rows with CAS claim / lease semantics."""

    KIND = "event_delivery"

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    @staticmethod
    def id_for(event_id: str, handler_key: str) -> str:
        return delivery_id(event_id, handler_key)

    def get_or_create(self, workspace_id: str, event_id: str,
                      handler_key: str) -> dict:
        wid = _text(workspace_id, "workspace_id")
        eid = _text(event_id, "event_id")
        key = _valid_handler_key(handler_key)
        did = delivery_id(eid, key)
        found = self._ctx.repo.get(self.KIND, wid, did)
        if found is not None:
            return found
        now = self._ctx.clock()
        row = {
            "id": did,
            "workspace_id": wid,
            "event_id": eid,
            "handler_key": key,
            "status": "pending",
            "attempts": 0,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }
        self._ctx.repo.put(self.KIND, wid, did, row)
        return row

    def for_event(self, workspace_id: str, event_id: str) -> list[dict]:
        wid = _text(workspace_id, "workspace_id")
        eid = _text(event_id, "event_id")
        return [row for row in self._ctx.repo.list(self.KIND, wid)
                if row.get("event_id") == eid]

    def list(self, workspace_id: str) -> list[dict]:
        wid = _text(workspace_id, "workspace_id")
        return sorted(self._ctx.repo.list(self.KIND, wid),
                      key=lambda row: str(row.get("id") or ""))

    def all_terminal(self, workspace_id: str, event_id: str) -> bool:
        rows = self.for_event(workspace_id, event_id)
        return all(row.get("status") in DELIVERY_TERMINAL for row in rows)

    # -- transitions ---------------------------------------------------

    def claim(self, delivery: dict, worker_id: str,
              lease_seconds: int = 30) -> dict | None:
        """CAS-claim a pending, due, unleased delivery for ``worker_id``."""
        worker = _text(worker_id, "worker_id")
        lease_seconds = _valid_lease_seconds(lease_seconds)
        wid = _text(delivery.get("workspace_id"), "workspace_id")
        did = _text(delivery.get("id"), "delivery id")
        got = self._ctx.repo.get_versioned(self.KIND, wid, did)
        if got is None:
            return None
        row, version = got
        row = dict(row)
        if row.get("status") in DELIVERY_TERMINAL:
            return None
        now = self._ctx.clock()
        if _leased(row, now) or not _due(row, now):
            return None
        row["claimed_by"] = worker
        row["claimed_at"] = now
        row["lease_expires_at"] = _lease_expiry(now, lease_seconds)
        row["updated_at"] = now
        try:
            self._ctx.repo.put_if_version(self.KIND, wid, did, row, version)
        except ConcurrencyConflictError:
            return None
        return row

    def mark_delivered(self, delivery: dict, worker_id: str) -> dict | None:
        """Terminal success; only the claim owner (or a stale claim) may finish."""
        return self._finish(delivery, worker_id, status="delivered")

    def mark_failed(self, delivery: dict, worker_id: str,
                    error: str) -> dict | None:
        """Terminal ``dead`` after the retry budget, else ``pending`` + backoff."""
        return self._finish(delivery, worker_id, status=None, error=error)

    def _finish(self, delivery: dict, worker_id: str, *, status: str | None,
                error: str = "") -> dict | None:
        worker = _text(worker_id, "worker_id")
        wid = _text(delivery.get("workspace_id"), "workspace_id")
        did = _text(delivery.get("id"), "delivery id")
        got = self._ctx.repo.get_versioned(self.KIND, wid, did)
        if got is None:
            return None
        row, version = got
        row = dict(row)
        if row.get("status") in DELIVERY_TERMINAL:
            return row
        owner = row.get("claimed_by")
        if owner and owner != worker:
            # another live claim owns the row; a stale lease is reclaimable
            if _leased(row, self._ctx.clock()):
                return None
        now = self._ctx.clock()
        row["claimed_by"] = None
        row["claimed_at"] = None
        row["lease_expires_at"] = None
        row["updated_at"] = now
        if status == "delivered":
            row["status"] = "delivered"
            row["next_attempt_at"] = None
            row["last_error"] = None
        else:
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["last_error"] = str(error or "handler failed")
            if row["attempts"] >= DELIVERY_MAX_ATTEMPTS:
                row["status"] = "dead"
                row["next_attempt_at"] = None
            else:
                row["status"] = "pending"
                row["next_attempt_at"] = _next_attempt(row["attempts"], now)
        try:
            self._ctx.repo.put_if_version(self.KIND, wid, did, row, version)
        except ConcurrencyConflictError:
            return None
        return row


__all__ = [
    "DELIVERY_MAX_ATTEMPTS",
    "DELIVERY_TERMINAL",
    "HANDLER_KINDS",
    "DeliveryTracker",
    "HandlerRegistry",
    "Transport",
    "delivery_id",
    "send_http_handler",
]
