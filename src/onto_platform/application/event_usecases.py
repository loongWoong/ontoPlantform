"""Event use cases: declare contracts, publish, subscribe and emit (P1-Event).

The platform's outbox/Dispatcher already carries lifecycle events; this module
adds the *business* event chain promised by the architecture doc §7.3:

    CreateEventContract -> PublishEventContract -> SubscribeEvent
    -> EmitEvent -> (outbox) -> Dispatcher -> handler_key

Contracts and subscriptions are ordinary workspace documents (kind
``event_contract`` / ``event_subscription``), so they inherit tenancy,
authorization and storage from the rest of the platform.  A contract's name is
its identity inside a workspace (the document id), because the business event
name is what emitters and subscribers refer to; subscriptions get a generated
id because one event can have many.

Wiring into the facade/API is intentionally left to the composition root.
"""

from __future__ import annotations

from ..domain.event import (
    CONTRACT_LIFECYCLES,
    EventContract,
    EventSubscription,
    validate_event_name,
)
from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext
from .events import EventLog, subscriptions_for_event


def _contract_row(ctx: ApplicationContext, workspace_id: str,
                  event_type: str) -> tuple[dict, EventContract]:
    row = ctx.repo.get("event_contract", workspace_id, event_type)
    if row is None:
        raise NotFoundError(f"event contract not found: {event_type!r}")
    contract = EventContract.from_dict(row)
    return row, contract


def _published_contract(ctx: ApplicationContext, workspace_id: str,
                        event_type: str) -> tuple[dict, EventContract]:
    row, contract = _contract_row(ctx, workspace_id, event_type)
    if contract.lifecycle != "published":
        raise ValidationError(
            f"event contract {event_type!r} is {contract.lifecycle!r}; "
            "publish it before subscribing or emitting"
        )
    return row, contract


class CreateEventContract:
    """Declare a business event contract as a draft (editor only)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str,
                 aggregate_kind: str, payload_schema: dict,
                 description: str = "", version: int = 1) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        validate_event_name(name)
        if self._ctx.repo.get("event_contract", workspace_id, name) is not None:
            raise ValidationError(f"event contract already exists: {name!r}")
        now = self._ctx.clock()
        contract = EventContract(
            name=name,
            version=version,
            description=description,
            aggregate_kind=aggregate_kind,
            payload_schema=payload_schema,
            lifecycle="draft",
            created_at=now,
            updated_at=now,
        )
        row = {**contract.to_dict(), "id": contract.name,
               "workspace_id": workspace_id}
        self._ctx.save("event_contract", row)
        self._ctx.audit_event(
            actor, workspace_id, "event_contract.create", contract.name,
            {"event_type": contract.name, "version": contract.version,
             "aggregate_kind": contract.aggregate_kind,
             "payload_fields": sorted(contract.payload_schema)},
        )
        return row


class PublishEventContract:
    """Promote a draft contract to ``published`` (draft -> published).

    Publishing an already-published contract is an explicit error rather than
    a silent no-op: the published document is what emitters and subscribers
    depend on, so a second publish must be a deliberate new version decision,
    not an accidental overwrite.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        row, contract = _contract_row(self._ctx, workspace_id, name)
        if contract.lifecycle == "published":
            raise ValidationError(
                f"event contract {name!r} is already published")
        if contract.lifecycle not in CONTRACT_LIFECYCLES:
            raise ValidationError(
                f"event contract {name!r} lifecycle {contract.lifecycle!r} "
                "cannot be published")
        contract.lifecycle = "published"
        contract.updated_at = self._ctx.clock()
        row = {**row, **contract.to_dict()}
        self._ctx.save("event_contract", row)
        self._ctx.audit_event(
            actor, workspace_id, "event_contract.publish", contract.name,
            {"event_type": contract.name, "version": contract.version},
        )
        return row


class ListEventContracts:
    """List workspace event contracts, optionally filtered by lifecycle."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 lifecycle: str | None = None) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        rows = self._ctx.repo.list("event_contract", workspace_id)
        if lifecycle:
            rows = [row for row in rows if row.get("lifecycle") == lifecycle]
        return sorted(rows, key=lambda row: str(row.get("name") or ""))


class SubscribeEvent:
    """Subscribe to a published contract with an optional payload filter."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str,
                 event_type: str, filter: dict | None = None,
                 handler_key: str = "", enabled: bool = True) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        name = str(name or "").strip()
        if not name:
            raise ValidationError("event subscription name must not be empty")
        validate_event_name(event_type)
        _row, contract = _published_contract(self._ctx, workspace_id, event_type)
        if filter is None:
            filter = {}
        if not isinstance(filter, dict):
            raise ValidationError("event subscription filter must be an object")
        unknown = sorted(set(filter) - set(contract.payload_schema))
        if unknown:
            raise ValidationError(
                "event subscription filter fields are not part of event contract "
                f"{event_type!r}: {unknown}")
        if not isinstance(enabled, bool):
            raise ValidationError("event subscription enabled must be a boolean")
        subscription = EventSubscription(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name,
            event_type=event_type,
            filter=dict(filter),
            handler_key=handler_key,
            enabled=enabled,
            created_at=self._ctx.clock(),
        )
        self._ctx.save("event_subscription", subscription.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "event_subscription.create", subscription.id,
            {"name": subscription.name, "event_type": subscription.event_type,
             "handler_key": subscription.handler_key,
             "filter": dict(subscription.filter)},
        )
        return subscription.to_dict()


class ListEventSubscriptions:
    """List workspace event subscriptions (subscriptions are readable)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 event_type: str | None = None) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        rows = self._ctx.repo.list("event_subscription", workspace_id)
        if event_type:
            rows = [row for row in rows if row.get("event_type") == event_type]
        return sorted(rows, key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("name") or ""),
            str(row.get("id") or ""),
        ))


class DeleteEventSubscription:
    """Remove one subscription; its handler registration is process-local."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 subscription_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        row = self._ctx.must_get("event_subscription", workspace_id,
                                 subscription_id)
        deleted = self._ctx.repo.delete("event_subscription", workspace_id,
                                        subscription_id)
        if not deleted:  # pragma: no cover - must_get already proved presence
            raise NotFoundError(
                f"event subscription not found: {subscription_id!r}")
        self._ctx.audit_event(
            actor, workspace_id, "event_subscription.delete", subscription_id,
            {"name": row.get("name"), "event_type": row.get("event_type")},
        )
        return {"deleted": True, "id": subscription_id}


class EmitEvent:
    """Emit one business event through the contract and the transactional outbox.

    The event is appended (outbox) and the matching subscriptions are computed
    for the caller; delivery is the Dispatcher's job, so ``EmitEvent`` does not
    invoke handlers itself.  The audit row records ids/type only - never the
    payload body, which may carry business data.
    """

    def __init__(self, ctx: ApplicationContext,
                 log: EventLog | None = None) -> None:
        self._ctx = ctx
        self._log = log or EventLog(ctx)

    def __call__(self, workspace_id: str, actor: str, event_type: str,
                 aggregate_kind: str, aggregate_id: str,
                 payload: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        validate_event_name(event_type)
        aggregate_kind = str(aggregate_kind or "").strip()
        aggregate_id = str(aggregate_id or "").strip()
        if not aggregate_kind:
            raise ValidationError("aggregate_kind must not be empty")
        if not aggregate_id:
            raise ValidationError("aggregate_id must not be empty")
        _row, contract = _published_contract(self._ctx, workspace_id, event_type)
        if contract.aggregate_kind != aggregate_kind:
            raise ValidationError(
                f"aggregate_kind {aggregate_kind!r} does not match event contract "
                f"{event_type!r}, which declares {contract.aggregate_kind!r}")
        candidates = {} if payload is None else payload
        if not isinstance(candidates, dict):
            raise ValidationError("event payload must be an object")
        event = self._log.emit(
            event_type, workspace_id, actor, aggregate_kind, aggregate_id,
            payload=candidates, contract=contract,
        )
        matched = [subscription.to_dict()
                   for subscription in subscriptions_for_event(self._ctx, event)]
        self._ctx.audit_event(
            actor, workspace_id, "event.emit", event["id"],
            {"event_id": event["id"], "type": event_type,
             "aggregate_kind": aggregate_kind, "aggregate_id": aggregate_id,
             "contract_version": event["event_version"],
             "matched_subscriptions": [subscription["id"]
                                       for subscription in matched]},
        )
        return {**event, "matched_subscriptions": matched}
