"""Event bridge (P2.6): internal outbox events -> external HTTP delivery.

The bridge is a thin configuration layer on top of the existing webhook
delivery machinery.  It does **not** implement retries, leases, quotas or dead
letters: every bridge owns one internal ``webhook`` row (``source_bridge_id``
points back to the bridge) and every delivery goes through
``WebhookService.dispatch_webhooks`` and its ``webhook_delivery`` ledger.  The
bridge therefore inherits the ledger's at-least-once retry, exponential
backoff, rate limiting, replay and dead-letter behaviour for free, and a
bridge cannot drift from a plain webhook in how it is delivered.

``transform(event)`` is the mapping layer: it builds the external JSON body
from an outbox event, optionally wrapping it in an envelope and projecting
payload fields through ``field_map`` (``{"external_field": "payload.path"}``).
A missing source path becomes ``None`` instead of failing the delivery.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext
from .events import EVENT_CATALOG
from .security.distribution import WebhookService

BRIDGE_KIND = "event_bridge"
BRIDGE_KINDS = ("webhook", "http")


def _new_id(ctx: ApplicationContext, prefix: str) -> str:
    factory = getattr(ctx, "id_factory", None)
    if callable(factory):
        try:
            return f"{prefix}_{factory()}"
        except TypeError:  # pragma: no cover - alternate factory contract
            pass
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex[:12]}"


def _clock_of(ctx: ApplicationContext):
    clock = getattr(ctx, "clock", None)
    if callable(clock):
        return clock
    from datetime import datetime, timezone

    return lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"event bridge {label} must be a non-empty string")
    return value.strip()


def _validate_field_map(field_map: Any) -> dict[str, str]:
    """Validate ``{external_field: payload.path}`` (dot path, no empty parts)."""
    if field_map is None:
        return {}
    if not isinstance(field_map, Mapping):
        raise ValidationError("field_map must be an object")
    normalized: dict[str, str] = {}
    for external, path in field_map.items():
        external_name = str(external).strip()
        if not external_name:
            raise ValidationError(
                "field_map external field names must be non-empty strings")
        if not isinstance(path, str) or not path.strip():
            raise ValidationError(
                f"field_map source path for {external_name!r} must be a "
                "non-empty string")
        segments = path.strip().split(".")
        if any(not segment for segment in segments):
            raise ValidationError(
                f"field_map source path for {external_name!r} has an empty "
                f"segment: {path!r}")
        normalized[external_name] = path.strip()
    return normalized


def _validate_bridge_event_types(ctx: ApplicationContext, workspace_id: str,
                                 event_types: Any) -> list[str]:
    """Event types must be platform catalog events or *published* contracts."""
    if isinstance(event_types, str) or not isinstance(
            event_types, (list, tuple, set, frozenset)):
        raise ValidationError("event_types must be a list of event type names")
    normalized: list[str] = []
    for raw in event_types:
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationError("event_types entries must be non-empty strings")
        name = raw.strip()
        if name in normalized:
            raise ValidationError(f"duplicate event type: {name!r}")
        if name not in EVENT_CATALOG:
            contract = ctx.repo.get("event_contract", workspace_id, name)
            if contract is None:
                raise ValidationError(f"unknown event type: {name!r}")
            if contract.get("lifecycle") != "published":
                raise ValidationError(
                    f"event contract {name!r} is not published")
        normalized.append(name)
    if not normalized:
        raise ValidationError("event_types must not be empty")
    return normalized


def _value_at(event: Mapping[str, Any], path: str) -> Any:
    """Read a dotted path from the event envelope; missing -> ``None``."""
    current: Any = event
    for segment in path.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        else:
            return None
    return deepcopy(current)


class EventBridgeService:
    """Manage event bridges and dispatch them through the webhook ledger."""

    def __init__(self, ctx: ApplicationContext,
                 webhooks: WebhookService | None = None) -> None:
        self.ctx = ctx
        self.webhooks = webhooks or WebhookService(ctx)
        self._clock = _clock_of(ctx)

    # ---- helpers ----------------------------------------------------------

    def _require_manage(self, actor: str, workspace_id: str) -> None:
        self.ctx.require_action(actor, workspace_id, "security.manage")

    def _rows(self, workspace_id: str | None = None) -> list[dict]:
        rows = [deepcopy(dict(row)) for row in
                self.ctx.repo.list(BRIDGE_KIND, workspace_id)]
        rows.sort(key=lambda row: (str(row.get("created_at") or ""),
                                   str(row.get("id") or "")))
        return rows

    def _get_row(self, workspace_id: str, bridge_id: str) -> dict:
        row = self.ctx.repo.get(BRIDGE_KIND, workspace_id, bridge_id)
        if row is None:
            raise NotFoundError(f"event bridge not found: {bridge_id!r}")
        return deepcopy(dict(row))

    def _mark_internal_webhook(self, workspace_id: str, webhook_id: str,
                               bridge_id: str) -> None:
        row = self.ctx.repo.get("webhook", workspace_id, webhook_id)
        if row is None:  # pragma: no cover - created immediately before
            return
        row = deepcopy(dict(row))
        row["source_bridge_id"] = bridge_id
        self.ctx.repo.put("webhook", workspace_id, webhook_id, row)

    def _audit(self, actor: str, workspace_id: str, action: str, target: str,
               detail: dict | None = None) -> None:
        writer = getattr(self.ctx, "audit_event", None)
        if callable(writer):
            writer(actor, workspace_id, action, target, dict(detail or {}))

    # ---- CRUD -------------------------------------------------------------

    def create(self, workspace_id: str, actor: str, name: str,
               event_types: list[str], url: str, secret: str = "", *,
               field_map: dict[str, str] | None = None,
               include_envelope: bool = True,
               active: bool = True) -> dict:
        """Create a bridge and its internal webhook.

        The webhook is created *first* (it enforces ``security.manage``, URL
        hardening and event-type validity), so a rejected bridge never leaves a
        half-registered configuration behind.  The bridge only stores the
        subscription id; all delivery state lives on the webhook ledger rows.
        """
        self._require_manage(actor, workspace_id)
        name = _text(name, "name")
        event_types = _validate_bridge_event_types(
            self.ctx, workspace_id, event_types)
        field_map = _validate_field_map(field_map)
        if not isinstance(include_envelope, bool):
            raise ValidationError("include_envelope must be a boolean")
        if not isinstance(active, bool):
            raise ValidationError("active must be a boolean")
        secret = str(secret or "")

        hook = self.webhooks.create_webhook(
            workspace_id, actor, url, event_types, secret)
        if not active:
            self.webhooks.update_webhook(
                workspace_id, actor, hook["id"], active=False)

        now = self._clock()
        bridge_id = _new_id(self.ctx, "bridge")
        row = {
            "id": bridge_id,
            "workspace_id": workspace_id,
            "name": name,
            "kind": "webhook",
            "event_types": list(event_types),
            "url": str(hook.get("url") or url),
            "field_map": dict(field_map),
            "include_envelope": include_envelope,
            "active": active,
            "webhook_id": hook["id"],
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
        }
        self._mark_internal_webhook(workspace_id, hook["id"], bridge_id)
        self.ctx.save(BRIDGE_KIND, row)
        self._audit(actor, workspace_id, "event_bridge.created", bridge_id,
                    {"name": name, "event_types": event_types,
                     "webhook_id": hook["id"], "active": active})
        return deepcopy(row)

    def list(self, workspace_id: str, actor: str | None = None) -> list[dict]:
        """List bridges; ``actor=None`` is the internal dispatcher view."""
        if actor is not None:
            self._require_manage(actor, workspace_id)
        return self._rows(workspace_id)

    def get(self, workspace_id: str, actor: str, bridge_id: str) -> dict:
        self._require_manage(actor, workspace_id)
        return self._get_row(workspace_id, bridge_id)

    def update(self, workspace_id: str, actor: str, bridge_id: str, *,
               name: str | None = None,
               event_types: list[str] | None = None,
               url: str | None = None,
               secret: str | None = None,
               field_map: dict[str, str] | None = None,
               include_envelope: bool | None = None,
               active: bool | None = None) -> dict:
        """Update a bridge; subscription fields are synced to its webhook."""
        self._require_manage(actor, workspace_id)
        row = self._get_row(workspace_id, bridge_id)
        changes: dict[str, Any] = {}
        webhook_updates: dict[str, Any] = {}

        if name is not None:
            changes["name"] = _text(name, "name")
        if event_types is not None:
            changes["event_types"] = _validate_bridge_event_types(
                self.ctx, workspace_id, event_types)
            webhook_updates["event_types"] = changes["event_types"]
        if url is not None:
            webhook_updates["url"] = str(url)
            changes["url"] = str(url)
        if secret is not None:
            webhook_updates["secret"] = str(secret or "")
        if active is not None:
            if not isinstance(active, bool):
                raise ValidationError("active must be a boolean")
            changes["active"] = active
            webhook_updates["active"] = active
        if field_map is not None:
            changes["field_map"] = _validate_field_map(field_map)
        if include_envelope is not None:
            if not isinstance(include_envelope, bool):
                raise ValidationError("include_envelope must be a boolean")
            changes["include_envelope"] = include_envelope

        if webhook_updates:
            hook = self.webhooks.update_webhook(
                workspace_id, actor, row["webhook_id"], **webhook_updates)
            # Keep the bridge URL in sync with URL normalization.
            changes["url"] = hook.get("url") or changes.get("url")
        updated = {**row, **changes, "updated_at": self._clock()}
        self.ctx.save(BRIDGE_KIND, updated)
        self._audit(actor, workspace_id, "event_bridge.updated", bridge_id,
                    {"changed": sorted(changes)})
        return deepcopy(updated)

    def delete(self, workspace_id: str, actor: str, bridge_id: str) -> dict:
        """Deactivate a bridge and its internal webhook (ledger rows kept)."""
        self._require_manage(actor, workspace_id)
        row = self._get_row(workspace_id, bridge_id)
        now = self._clock()
        updated = {**row, "active": False, "deleted_at": now,
                   "updated_at": now}
        self.ctx.save(BRIDGE_KIND, updated)
        self.webhooks.update_webhook(
            workspace_id, actor, row["webhook_id"], active=False)
        self._audit(actor, workspace_id, "event_bridge.deleted", bridge_id,
                    {"webhook_id": row.get("webhook_id")})
        return {"id": bridge_id, "deleted": True, "webhook_deactivated": True}

    # ---- transform --------------------------------------------------------

    def transform(self, event: Mapping[str, Any],
                  bridge: Mapping[str, Any] | str | None = None) -> dict:
        """Build the external body for ``event``.

        ``bridge`` is a bridge row (or its id); when omitted the identity
        envelope with the full payload is returned.  With a ``field_map`` the
        payload is projected field-by-field (missing source paths become
        ``None``), otherwise it is copied verbatim.
        """
        if not isinstance(event, Mapping):
            raise ValidationError("event must be an object")
        field_map: dict[str, str] = {}
        include_envelope = True
        if bridge is not None:
            if isinstance(bridge, str):
                wanted = bridge
                bridge = next(
                    (row for row in self._rows()
                     if str(row.get("id") or "") == wanted), None)
                if bridge is None:
                    raise NotFoundError(
                        f"event bridge not found: {wanted!r}")
            if not isinstance(bridge, Mapping):
                raise ValidationError("bridge must be an object or an id")
            field_map = _validate_field_map(bridge.get("field_map"))
            include_envelope = bool(bridge.get("include_envelope", True))

        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        if field_map:
            mapped = {external: _value_at(event, path)
                      for external, path in field_map.items()}
        else:
            mapped = deepcopy(dict(payload))

        if not include_envelope:
            return mapped
        try:
            version = int(event.get("event_version") or 1)
        except (TypeError, ValueError):
            version = 1
        return {
            "id": str(event.get("event_id") or event.get("id") or ""),
            "type": str(event.get("type") or ""),
            "version": version,
            "occurredAt": str(event.get("ts") or event.get("occurred_at") or ""),
            "workspaceId": str(event.get("workspace_id") or ""),
            "payload": mapped,
        }

    # ---- dispatch ---------------------------------------------------------

    def dispatch(self, workspace_id: str | None = None) -> dict:
        """Run the webhook dispatcher and report how many bridges it covers.

        No retry/lease logic is duplicated here: the internal webhook rows are
        ordinary webhooks, so ``dispatch_webhooks`` performs all delivery,
        quota, backoff and dead-letter work on the shared ledger.
        """
        result = dict(self.webhooks.dispatch_webhooks(workspace_id))
        bridges = [row for row in self._rows(workspace_id)
                   if row.get("active") and not row.get("deleted_at")]
        result["bridges"] = len(bridges)
        per_workspace: dict[str, int] = {}
        for row in bridges:
            workspace = str(row.get("workspace_id") or "")
            per_workspace[workspace] = per_workspace.get(workspace, 0) + 1
        detail = {key: result.get(key) for key in
                  ("delivered", "failed", "skipped", "throttled",
                   "dead_lettered")}
        for workspace, count in per_workspace.items():
            self._audit("system", workspace, "event_bridge.dispatched",
                        "event_bridge", {"bridges": count, **detail})
        return result


__all__ = [
    "BRIDGE_KINDS",
    "BRIDGE_KIND",
    "EventBridgeService",
]
