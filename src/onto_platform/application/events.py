"""Domain events + transactional outbox (plan-v2 1.3).

Every write action emits one event envelope in the SAME transaction that
saves the aggregate (``ctx.transaction()``).  Events are stored as ordinary
repository documents (kind ``event``) with ``dispatched_at`` empty - that is
the outbox: a dispatcher later delivers them to subscribers and stamps
``dispatched_at``; a crash in between leaves them undispatched, so delivery
is at-least-once and retryable (plan §1.3 test: publish_survives_crash).

Two kinds of events share this machinery:

* platform lifecycle events, catalogued in :data:`EVENT_CATALOG` (unknown
  types are refused), and
* business events declared by a workspace as an
  :class:`~onto_platform.domain.event.model.EventContract`; emitting with
  ``contract=`` validates the payload and stamps ``event_version``/``contract``.

Subscriptions are either in-process, registered by ``Dispatcher.subscribe``
(platform lifecycle events) or declared as ``event_subscription`` documents
that filter a contract's payload and name an in-process handler through
``Dispatcher.subscribe_key``.
"""

from __future__ import annotations

from typing import Callable

from ..domain.event import EventContract, EventSubscription, matching_subscriptions
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
    "capability.deleted": 1,
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


def subscriptions_for_event(ctx: ApplicationContext,
                            event: dict) -> list[EventSubscription]:
    """Enabled, workspace-scoped subscriptions matching ``event``.

    A malformed stored row is ignored: one bad subscription document must not
    break the outbox for every other consumer.  ``event_type`` and the
    ``enabled`` flag are applied by ``matching_subscriptions`` on top of the
    payload filter.
    """
    subscriptions: list[EventSubscription] = []
    for row in ctx.repo.list("event_subscription", event.get("workspace_id")):
        try:
            subscriptions.append(EventSubscription.from_dict(row))
        except (ValidationError, KeyError, TypeError):
            continue
    return matching_subscriptions(subscriptions, event)


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
        contract: EventContract | None = None,
    ) -> dict:
        if contract is not None:
            # a business event never falls back to the platform catalogue; the
            # contract IS the declaration, and only a published one may serve
            if contract.name != event_type:
                raise ValidationError(
                    f"event type {event_type!r} does not match event contract "
                    f"{contract.name!r}"
                )
            if contract.lifecycle != "published":
                raise ValidationError(
                    f"event contract {contract.name!r} is {contract.lifecycle!r}; "
                    "only a published contract may emit events"
                )
            candidates = {} if payload is None else payload
            problems = contract.validate_payload(candidates)
            if problems:
                raise ValidationError(
                    f"event payload does not match contract {contract.name!r}: "
                    + "; ".join(problems)
                )
            event_version = contract.version
            stored_payload = candidates
        else:
            if event_type not in EVENT_CATALOG:
                raise ValidationError(f"unknown event type: {event_type!r}")
            event_version = EVENT_CATALOG[event_type]
            stored_payload = payload or {}
        if self._ctx.repo.get("workspace", None, workspace_id) is None:
            raise NotFoundError(f"workspace not found: {workspace_id!r}")
        event_id = f"evt_{self._ctx.id_factory()}"
        event = {
            "id": event_id,
            "event_id": event_id,
            "type": event_type,
            "event_version": event_version,
            "ts": occurred_at or self._ctx.clock(),
            "workspace_id": workspace_id,
            "actor": actor,
            "aggregate": {
                "kind": aggregate_kind,
                "id": aggregate_id,
                "version": aggregate_version,
            },
            "execution_id": execution_id,
            "payload": stored_payload,
            "dispatched_at": None,
            "attempts": 0,
            "last_error": None,
        }
        if contract is not None:
            event["contract"] = contract.name
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
        #: ``handler_key -> handler`` registry used by event_subscription rows
        self._handlers: dict[str, Subscriber] = {}

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def subscribe_key(self, handler_key: str, handler: Subscriber) -> None:
        """Register the in-process handler a subscription's ``handler_key`` names.

        Registering the same key again replaces the previous handler; a
        subscription whose key is not registered is deferred by ``dispatch``
        (counted in ``skipped`` and left in the outbox), never dropped.
        """
        key = str(handler_key or "").strip()
        if not key:
            raise ValidationError("handler_key must not be empty")
        self._handlers[key] = handler

    def dispatch(self, workspace_id: str | None = None,
                 limit: int = 100) -> dict:
        delivered, failed, skipped, dead = 0, 0, 0, 0
        errors: list[str] = []
        for event in self._log.pending(workspace_id, limit):
            handlers = list(self._subscribers.get(event["type"], []))
            subscriptions = subscriptions_for_event(self._ctx, event)
            resolved = [
                (subscription, self._handlers[subscription.handler_key])
                for subscription in subscriptions
                if subscription.handler_key in self._handlers
            ]
            unresolved = [
                subscription for subscription in subscriptions
                if subscription.handler_key not in self._handlers
            ]
            if not handlers and not subscriptions:
                # no subscriber is not a delivery: record that honestly
                # instead of stamping the outbox as if it were consumed
                skipped += 1
                self._log.mark_dispatched(event, delivery="no_subscribers")
                continue
            try:
                for handler in handlers:
                    handler(dict(event))
                for _subscription, handler in resolved:
                    handler(dict(event))
            except Exception as exc:  # a subscriber's failure must not lose the event
                failed += 1
                self._log.mark_failed(event, str(exc))
                errors.append(f"{event['type']}: {exc}")
                if event.get("dead_lettered_at"):
                    dead += 1
                continue
            if unresolved:
                # A matching subscription has no registered handler yet.  The
                # event is deferred, not consumed: it stays pending so the
                # handler can still receive it after registration.  (Delivery
                # is at-least-once, so handlers must stay idempotent by
                # event_id; inline handlers that already ran may run again.)
                skipped += 1
                event["last_error"] = (
                    "no handler registered for subscription(s): "
                    + ", ".join(sorted(
                        subscription.handler_key for subscription in unresolved))
                )
                self._ctx.repo.put("event", event["workspace_id"],
                                   event["id"], event)
                continue
            delivered += 1
            names = [getattr(handler, "__name__", type(handler).__name__)
                     for handler in handlers]
            names += [f"{subscription.name}:{subscription.handler_key}"
                      for subscription, _handler in resolved]
            self._log.mark_dispatched(event, delivery="delivered",
                                      consumer=",".join(sorted(names)))
        return {"dispatched": delivered, "failed": failed,
                "skipped": skipped, "dead_lettered": dead, "errors": errors}
