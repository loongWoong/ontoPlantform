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
``Dispatcher.subscribe_key``.  P2.2 adds durable dispatch state: an
``event_handler`` registry row (see :mod:`onto_platform.application.
event_handlers`) names which worker serves a ``handler_key``, and one
``event_delivery`` row per (event, handler) is CAS-claimed before it runs, so
multiple workers sharing one database never execute the same handler twice.
"""

from __future__ import annotations

from typing import Callable

from ..domain.event import EventContract, EventSubscription, matching_subscriptions
from ..errors import (
    ConcurrencyConflictError,
    NotFoundError,
    ValidationError,
)
from .common import ApplicationContext
from .event_handlers import (
    DeliveryTracker,
    HandlerRegistry,
    Transport,
    send_http_handler,
)


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


def _leased(event: dict, now: str) -> bool:
    """True while another worker's claim lease has not expired."""
    from datetime import datetime, timezone

    if not event.get("claimed_by"):
        return False
    value = event.get("lease_expires_at")
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
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
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
            and not _leased(e, now)
        ]
        return rows[:limit]

    def claim(self, event: dict, worker_id: str,
              lease_seconds: int = 30) -> dict | None:
        """Claim one pending event for ``worker_id`` with an optimistic lease.

        The outbox is ordinary versioned storage, so the claim is a
        ``get_versioned`` + ``put_if_version`` compare-and-set: two workers
        that race for the same row, only one wins.  A worker that crashes after
        claiming leaves the lease behind; once it expires another worker can
        claim the event again (at-least-once delivery).
        """
        worker_id = str(worker_id or "").strip()
        if not worker_id:
            raise ValidationError("worker_id must not be empty")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise ValidationError("lease_seconds must be an integer")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValidationError(
                f"lease_seconds must be between 1 and 3600, got {lease_seconds}")
        workspace_id = event.get("workspace_id")
        event_id = event.get("id") or event.get("event_id")
        if not workspace_id or not event_id:
            return None
        got = self._ctx.repo.get_versioned("event", workspace_id, event_id)
        if got is None:
            return None
        row, version = got
        row = dict(row)
        now = self._ctx.clock()
        if row.get("dispatched_at") or row.get("dead_lettered_at"):
            return None
        if _leased(row, now) or not _due(row, now):
            return None
        row["claimed_by"] = worker_id
        row["claimed_at"] = now
        row["lease_expires_at"] = _lease_expiry(now, lease_seconds)
        try:
            self._ctx.repo.put_if_version(
                "event", workspace_id, event_id, row, version)
        except ConcurrencyConflictError:
            return None
        return row

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
        event["claimed_by"] = None
        event["claimed_at"] = None
        event["lease_expires_at"] = None
        self._ctx.repo.put("event", event["workspace_id"], event["id"], event)

    def mark_failed(self, event: dict, error: str) -> None:
        event["attempts"] = int(event.get("attempts") or 0) + 1
        event["last_error"] = error
        event["claimed_by"] = None
        event["claimed_at"] = None
        event["lease_expires_at"] = None
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
    """Delivers undispatched events to registered subscribers.

    Legacy behaviour (``subscribe`` / ``subscribe_key``) is unchanged.  The
    durable path resolves ``event_handler`` registry rows for the event type,
    unions their ``handler_key`` values with matching ``event_subscription``
    keys, and gives every key one ``event_delivery`` row.  A delivery is
    CAS-claimed with a lease before it runs; an event is only stamped
    ``dispatched_at`` when *all* of its delivery rows are terminal.  A key with
    no local callback yet stays ``pending`` (never dropped), and delivery is
    at-least-once, so handlers must be idempotent by ``event_id``.
    """

    def __init__(self, ctx: ApplicationContext,
                 *, transport: Transport | None = None) -> None:
        self._ctx = ctx
        self._log = EventLog(ctx)
        self._subscribers: dict[str, list[Subscriber]] = {}
        #: ``handler_key -> handler`` registry used by event_subscription rows
        self._handlers: dict[str, Subscriber] = {}
        #: workspace-scoped callbacks registered through ``register_handler``;
        #: ``(None, key)`` keeps the P1 global fallback
        self._local_handlers: dict[tuple[str | None, str], Subscriber] = {}
        #: http deliveries are delivered through this transport; the default
        #: is the signed stdlib POST in :func:`send_http_handler`
        self._transport: Transport = transport or send_http_handler
        self.registry = HandlerRegistry(ctx)
        self.deliveries = DeliveryTracker(ctx)

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

    def register_handler(self, handler_key: str, handler: Subscriber,
                         workspace_id: str | None = None) -> None:
        """Register a local callback for a durable ``event_handler`` row.

        ``workspace_id`` scopes the callback to one workspace; ``None`` behaves
        like :meth:`subscribe_key` (any workspace).  The registry row itself is
        created through :meth:`HandlerRegistry.register`; this only says which
        worker executes it.
        """
        key = str(handler_key or "").strip()
        if not key:
            raise ValidationError("handler_key must not be empty")
        self._local_handlers[(workspace_id, key)] = handler

    def _local_handler(self, workspace_id: str | None,
                       handler_key: str) -> Subscriber | None:
        scoped = self._local_handlers.get((workspace_id, handler_key))
        if scoped is not None:
            return scoped
        return self._handlers.get(handler_key)

    def _release_claim(self, event: dict, note: str) -> None:
        """Give the outbox claim back so another worker can pick the event up."""
        event = dict(event)
        event["last_error"] = note
        event["claimed_by"] = None
        event["claimed_at"] = None
        event["lease_expires_at"] = None
        self._ctx.repo.put("event", event["workspace_id"], event["id"], event)

    def dispatch(self, workspace_id: str | None = None,
                 limit: int = 100, worker_id: str | None = None,
                 lease_seconds: int = 30) -> dict:
        delivered, failed, skipped, dead = 0, 0, 0, 0
        errors: list[str] = []
        for candidate in self._log.pending(workspace_id, limit):
            event = candidate
            if worker_id is not None:
                event = self._log.claim(candidate, worker_id, lease_seconds)
                if event is None:
                    # another worker won the compare-and-set; not a failure
                    continue
            handlers = list(self._subscribers.get(event["type"], []))
            subscriptions = subscriptions_for_event(self._ctx, event)
            registered = self.registry.resolve(event)
            keys = sorted(
                {str(row["handler_key"]) for row in registered}
                | {subscription.handler_key for subscription in subscriptions})
            if not handlers and not keys:
                # no subscriber is not a delivery: record that honestly
                # instead of stamping the outbox as if it were consumed
                skipped += 1
                self._log.mark_dispatched(event, delivery="no_subscribers")
                continue
            rows_by_key = {str(row["handler_key"]): row for row in registered}
            failures: list[str] = []
            try:
                for handler in handlers:
                    handler(dict(event))
            except Exception as exc:  # a subscriber's failure must not lose the event
                failures.append(f"{event['type']}: {exc}")
            executed: list[str] = []
            for key in keys:
                current = self.deliveries.get_or_create(
                    event["workspace_id"], event["id"], key)
                if current.get("status") in ("delivered", "dead"):
                    # terminal deliveries never execute again
                    continue
                row = rows_by_key.get(key)
                local = self._local_handler(event["workspace_id"], key)
                http_row = row is not None and row.get("kind") == "http"
                if local is None and not http_row:
                    # this worker cannot serve the key (yet): keep it pending
                    continue
                claimed = self.deliveries.claim(
                    current, worker_id or "local", lease_seconds)
                if claimed is None:
                    # another worker holds a live lease on this delivery
                    continue
                try:
                    if local is not None:
                        local(dict(event))
                    else:
                        self._transport({
                            "handler_key": key,
                            "url": str(row.get("endpoint") or ""),
                            "secret": str(row.get("secret") or ""),
                            "event": dict(event),
                        })
                except Exception as exc:
                    failures.append(f"{event['type']}:{key}: {exc}")
                    self.deliveries.mark_failed(
                        claimed, worker_id or "local", str(exc))
                    errors.append(f"{event['type']}:{key}: {exc}")
                    break
                self.deliveries.mark_delivered(claimed, worker_id or "local")
                executed.append(key)
            rows = self.deliveries.for_event(
                event["workspace_id"], event["id"])
            all_terminal = all(
                row.get("status") in ("delivered", "dead") for row in rows)
            if failures:
                failed += 1
                self._log.mark_failed(event, "; ".join(failures))
                if event.get("dead_lettered_at"):
                    dead += 1
                continue
            if not keys:
                # legacy path: only in-process subscribers, nothing durable
                delivered += 1
                names = [getattr(handler, "__name__", type(handler).__name__)
                         for handler in handlers]
                self._log.mark_dispatched(
                    event, delivery="delivered",
                    consumer=",".join(sorted(names)) or None)
                continue
            if not all_terminal:
                # a delivery is still pending (no local callback / live lease)
                # or waiting for another worker: defer the event, never drop it
                skipped += 1
                waiting = [str(row.get("handler_key")) for row in rows
                           if row.get("status") not in ("delivered", "dead")]
                self._release_claim(
                    event, "awaiting handler(s): " + ", ".join(sorted(waiting)))
                continue
            delivered += 1
            names = [getattr(handler, "__name__", type(handler).__name__)
                     for handler in handlers]
            names += [f"handler:{key}" for key in executed
                      if key not in {sub.handler_key
                                     for sub in subscriptions}]
            names += [f"{subscription.name}:{subscription.handler_key}"
                      for subscription in subscriptions
                      if subscription.handler_key in executed]
            self._log.mark_dispatched(event, delivery="delivered",
                                      consumer=",".join(sorted(names)) or None)
        return {"dispatched": delivered, "failed": failed,
                "skipped": skipped, "dead_lettered": dead, "errors": errors}
