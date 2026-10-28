"""Domain events + transactional outbox (plan-v2 1.3).

Every write action emits one event envelope in the SAME transaction that
saves the aggregate (``ctx.transaction()``).  Events are stored as ordinary
repository documents (kind ``event``) with ``dispatched_at`` empty - that is
the outbox: a dispatcher later delivers them to subscribers and stamps
``dispatched_at``; a crash in between leaves them undispatched, so delivery
is at-least-once and retryable (plan §1.3 test: publish_survives_crash).
"""

from __future__ import annotations

from typing import Callable

from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext

#: Catalogue: every event type the platform may emit, with its envelope
#: version.  Adding a type requires a new entry (unknown types are refused).
EVENT_CATALOG: dict[str, int] = {
    "workspace.created": 1,
    "workspace.member_added": 1,
    "ontology.published": 1,
    "ontology.revised": 1,
    "release.canary_deployed": 1,
    "release.canary_promoted": 1,
    "capability.published": 1,
    "capability.invoked": 1,
    "capability.verified": 1,
    "capability.contract_failed": 1,
    "release.cut": 1,
    "release.deployed": 1,
    "release.rolled_back": 1,
    "projection.run_completed": 1,
    "reconciliation.found_differences": 1,
    "application.objective_set": 1,
    "application.objective_breached": 1,
    "todo.resolved": 1,
    "todo.opened": 1,
    "schema.fingerprint_mismatch": 1,
    "schema.recovered": 1,
    "deployment.pending": 1,
    "deployment.failed": 1,
    "execution.resolved": 1,
    "controller.converged": 1,
    "controller.escalated": 1,
    "objective.evaluated": 1,
    "notification.delivery_failed": 1,
    "settings.changed": 1,
}

#: delivery contract: a failing event is retried with backoff, then parked in
#: the dead-letter set instead of being silently dropped or retried forever
MAX_DELIVERY_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 5


def _next_attempt(attempts: int, now: str) -> str:
    from datetime import datetime, timedelta, timezone

    delay = BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))
    try:
        base = datetime.fromisoformat(now)
    except ValueError:  # pragma: no cover - clock contract
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _due(event: dict, now: str) -> bool:
    from datetime import datetime, timezone

    value = event.get("next_attempt_at")
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


class EventLog:
    """Appends and lists domain events (the outbox table)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def emit(
        self,
        event_type: str,
        workspace_id: str,
        actor: str,
        aggregate_kind: str,
        aggregate_id: str,
        payload: dict | None = None,
        *,
        execution_id: str | None = None,
        aggregate_version: int | None = None,
        occurred_at: str | None = None,
    ) -> dict:
        if event_type not in EVENT_CATALOG:
            raise ValidationError(f"unknown event type: {event_type!r}")
        if self._ctx.repo.get("workspace", None, workspace_id) is None:
            raise NotFoundError(f"workspace not found: {workspace_id!r}")
        event_id = f"evt_{self._ctx.id_factory()}"
        event = {
            "id": event_id,
            "event_id": event_id,
            "type": event_type,
            "event_version": EVENT_CATALOG[event_type],
            "ts": occurred_at or self._ctx.clock(),
            "workspace_id": workspace_id,
            "actor": actor,
            "aggregate": {
                "kind": aggregate_kind,
                "id": aggregate_id,
                "version": aggregate_version,
            },
            "execution_id": execution_id,
            "payload": payload or {},
            "dispatched_at": None,
            "attempts": 0,
            "last_error": None,
        }
        self._ctx.repo.put("event", workspace_id, event["id"], event)
        return event

    def list(self, workspace_id: str, limit: int = 200,
             event_type: str | None = None) -> list[dict]:
        rows = self._ctx.repo.list("event", workspace_id)
        if event_type:
            rows = [e for e in rows if e["type"] == event_type]
        return rows[-limit:]

    def pending(self, workspace_id: str | None = None,
                limit: int = 100) -> list[dict]:
        now = self._ctx.clock()
        rows = [
            e for e in self._ctx.repo.list("event", workspace_id)
            if not e.get("dispatched_at")
            and not e.get("dead_lettered_at")
            and _due(e, now)
        ]
        return rows[:limit]

    def dead_letters(self, workspace_id: str | None = None) -> list[dict]:
        return [e for e in self._ctx.repo.list("event", workspace_id)
                if e.get("dead_lettered_at")]

    def mark_dispatched(self, event: dict, *, delivery: str = "delivered",
                        consumer: str | None = None) -> None:
        event["dispatched_at"] = self._ctx.clock()
        event["delivery"] = delivery
        if consumer is not None:
            event["consumer"] = consumer
        event["next_attempt_at"] = None
        self._ctx.repo.put("event", event["workspace_id"], event["id"], event)

    def mark_failed(self, event: dict, error: str) -> None:
        event["attempts"] = int(event.get("attempts") or 0) + 1
        event["last_error"] = error
        now = self._ctx.clock()
        if event["attempts"] >= MAX_DELIVERY_ATTEMPTS:
            event["dead_lettered_at"] = now
            event["delivery"] = "dead_letter"
            event["next_attempt_at"] = None
        else:
            event["next_attempt_at"] = _next_attempt(event["attempts"], now)
        self._ctx.repo.put("event", event["workspace_id"], event["id"], event)


#: A subscriber receives the full envelope and must be idempotent by
#: ``event_id`` (delivery is at-least-once).
Subscriber = Callable[[dict], None]


class Dispatcher:
    """Delivers undispatched events to registered subscribers."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx
        self._log = EventLog(ctx)
        self._subscribers: dict[str, list[Subscriber]] = {}

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def dispatch(self, workspace_id: str | None = None,
                 limit: int = 100) -> dict:
        delivered, failed, skipped, dead = 0, 0, 0, 0
        errors: list[str] = []
        for event in self._log.pending(workspace_id, limit):
            handlers = list(self._subscribers.get(event["type"], []))
            if not handlers:
                # no subscriber is not a delivery: record that honestly
                # instead of stamping the outbox as if it were consumed
                skipped += 1
                self._log.mark_dispatched(event, delivery="no_subscribers")
                continue
            try:
                for handler in handlers:
                    handler(dict(event))
            except Exception as exc:  # a subscriber's failure must not lose the event
                failed += 1
                self._log.mark_failed(event, str(exc))
                errors.append(f"{event['type']}: {exc}")
                if event.get("dead_lettered_at"):
                    dead += 1
            else:
                delivered += 1
                names = ",".join(sorted(
                    getattr(handler, "__name__", type(handler).__name__)
                    for handler in handlers))
                self._log.mark_dispatched(event, delivery="delivered",
                                          consumer=names)
        return {"dispatched": delivered, "failed": failed,
                "skipped": skipped, "dead_lettered": dead, "errors": errors}
