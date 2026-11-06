"""Event contracts and subscriptions: business events as declared schemas.

Platform lifecycle events (``workspace.created``, ``ontology.published``, ...)
are catalogued in :mod:`onto_platform.application.events` and flow through the
transactional outbox.  A *business* event contract is declared by a workspace
editor (``traffic.vehicle_entered``), carries a versioned payload schema and a
declared aggregate kind, and is delivered by subscriptions that filter on
payload fields.

The same outbox carries both worlds: ``EventLog.emit(..., contract=...)``
validates the payload against the contract and stamps the envelope with
``event_version``/``contract``.  This module is pure domain - no repository,
no clock, no I/O; the application layer supplies ids/timestamps and persists
the documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ...errors import ValidationError

#: Business event name: lower-case dotted segments (``traffic.vehicle_entered``).
EVENT_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
_EVENT_NAME_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")

#: Payload field types the platform can check without an ontology runtime.
PAYLOAD_TYPES = ("string", "number", "integer", "boolean", "object", "array")

#: Contract lifecycles this module knows about (publish is draft -> published).
CONTRACT_LIFECYCLES = ("draft", "published")


def validate_event_name(name: Any) -> str:
    """Return ``name`` when it is a valid business event name, else raise.

    The pattern requires at least two dotted segments so an event name can
    never collide with (or be mistaken for) a plain object type / field name.
    """
    if not isinstance(name, str) or not _EVENT_NAME_RE.fullmatch(name):
        raise ValidationError(
            f"invalid event name {name!r}: expected lower-case dotted segments "
            f"like 'traffic.vehicle_entered' (pattern {EVENT_NAME_PATTERN})"
        )
    return name


def _is_base_type(value: Any, type_name: str) -> bool:
    """Base JSON type check; ``bool`` is not an ``int``/``number`` here."""
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    return False


def validate_payload_schema(schema: Any) -> dict[str, dict]:
    """Normalize and validate ``{field: spec}``; raise ``ValidationError``.

    A spec is ``{"type": <PAYLOAD_TYPES>, "required": bool, "description": str}``.
    Only those three keys are kept, so stored contracts are deterministic.
    ``object``/``array`` fields are type-checked only (no nested schema).
    """
    if not isinstance(schema, dict):
        raise ValidationError("payload_schema must be an object mapping fields to specs")
    normalized: dict[str, dict] = {}
    for field_name, spec in schema.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise ValidationError(
                f"payload field name must be a non-empty string, got {field_name!r}"
            )
        if not isinstance(spec, dict):
            raise ValidationError(f"payload field {field_name!r} spec must be an object")
        type_name = spec.get("type")
        if type_name not in PAYLOAD_TYPES:
            raise ValidationError(
                f"payload field {field_name!r} type {type_name!r} is invalid: "
                f"expected one of {list(PAYLOAD_TYPES)}"
            )
        required = spec.get("required", False)
        if not isinstance(required, bool):
            raise ValidationError(
                f"payload field {field_name!r} 'required' must be a boolean"
            )
        description = spec.get("description", "")
        if not isinstance(description, str):
            raise ValidationError(
                f"payload field {field_name!r} 'description' must be a string"
            )
        normalized[field_name] = {
            "type": type_name,
            "required": required,
            "description": description,
        }
    return normalized


@dataclass(slots=True)
class EventContract:
    """A workspace-declared business event: name, version and payload schema.

    ``aggregate_kind`` is the object type (or platform aggregate) an event of
    this contract is about; the emitter must agree with it.  ``version``
    becomes the outbox envelope's ``event_version``.
    """

    name: str
    version: int = 1
    description: str = ""
    aggregate_kind: str = ""
    payload_schema: dict[str, dict] = field(default_factory=dict)
    lifecycle: str = "draft"
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        validate_event_name(self.name)
        if not isinstance(self.version, int) or isinstance(self.version, bool) \
                or self.version <= 0:
            raise ValidationError(
                f"event contract version must be a positive integer, got {self.version!r}"
            )
        if not isinstance(self.description, str):
            raise ValidationError("event contract description must be a string")
        if not isinstance(self.aggregate_kind, str) or not self.aggregate_kind.strip():
            raise ValidationError(
                "event contract aggregate_kind must be a non-empty string"
            )
        self.aggregate_kind = self.aggregate_kind.strip()
        self.payload_schema = validate_payload_schema(self.payload_schema)
        if self.lifecycle not in CONTRACT_LIFECYCLES:
            raise ValidationError(
                f"event contract lifecycle {self.lifecycle!r} is invalid: "
                f"expected one of {list(CONTRACT_LIFECYCLES)}"
            )
        if not isinstance(self.created_at, str) or not isinstance(self.updated_at, str):
            raise ValidationError("event contract timestamps must be strings")

    def validate_payload(self, payload: Any) -> list[str]:
        """Return every contract violation for ``payload`` (empty = valid).

        Covers required fields, unknown fields and base JSON types; nested
        ``object``/``array`` contents are intentionally not inspected.
        """
        if not isinstance(payload, dict):
            return ["payload must be an object"]
        problems: list[str] = []
        for field_name, spec in self.payload_schema.items():
            if field_name not in payload:
                if spec["required"]:
                    problems.append(f"missing required field: {field_name}")
                continue
            if not _is_base_type(payload[field_name], spec["type"]):
                problems.append(
                    f"field {field_name!r} must be of type {spec['type']!r}"
                )
        for field_name in payload:
            if field_name not in self.payload_schema:
                problems.append(f"unknown field: {field_name}")
        return problems

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "aggregate_kind": self.aggregate_kind,
            "payload_schema": {
                key: dict(spec) for key, spec in self.payload_schema.items()
            },
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EventContract":
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ValidationError("event contract document must carry a string 'name'")
        return cls(
            name=data["name"],
            version=data.get("version", 1),
            description=data.get("description", ""),
            aggregate_kind=data.get("aggregate_kind", ""),
            payload_schema=data.get("payload_schema") or {},
            lifecycle=data.get("lifecycle", "draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass(slots=True)
class EventSubscription:
    """A workspace's subscription to one published event contract.

    ``filter`` maps *payload field -> expected value*; a list means "the field
    value is in this list".  ``handler_key`` names an in-process handler
    registered with ``Dispatcher.subscribe_key``; a subscription whose handler
    is missing is skipped (kept pending), never silently delivered.
    """

    id: str
    workspace_id: str
    name: str
    event_type: str
    filter: dict = field(default_factory=dict)
    handler_key: str = ""
    enabled: bool = True
    created_at: str = ""

    def __post_init__(self) -> None:
        for label, value in (
            ("id", self.id),
            ("workspace_id", self.workspace_id),
            ("name", self.name),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    f"event subscription {label} must be a non-empty string"
                )
        validate_event_name(self.event_type)
        if not isinstance(self.handler_key, str) or not self.handler_key.strip():
            raise ValidationError("event subscription handler_key must not be empty")
        self.handler_key = self.handler_key.strip()
        if not isinstance(self.filter, dict):
            raise ValidationError("event subscription filter must be an object")
        normalized: dict[str, Any] = {}
        for key, value in self.filter.items():
            if not isinstance(key, str) or not key.strip():
                raise ValidationError(
                    "event subscription filter keys must be non-empty strings"
                )
            if isinstance(value, (list, tuple)):
                if not value:
                    raise ValidationError(
                        f"event subscription filter {key!r} list must not be empty"
                    )
                normalized[key] = list(value)
            elif isinstance(value, dict):
                raise ValidationError(
                    f"event subscription filter {key!r} must be a scalar or a "
                    "list of values"
                )
            else:
                normalized[key] = value
        self.filter = normalized
        if not isinstance(self.enabled, bool):
            raise ValidationError("event subscription enabled must be a boolean")
        if not isinstance(self.created_at, str):
            raise ValidationError("event subscription created_at must be a string")

    def matches(self, event: dict) -> bool:
        """Whether ``event``'s payload satisfies every filter entry.

        The event type and ``enabled`` flag are deliberately not checked here;
        :func:`matching_subscriptions` applies those on top.
        """
        if not isinstance(event, dict):
            return False
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        for field_name, expected in self.filter.items():
            actual = payload.get(field_name)
            if isinstance(expected, (list, tuple)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "event_type": self.event_type,
            "filter": {key: value for key, value in self.filter.items()},
            "handler_key": self.handler_key,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EventSubscription":
        if not isinstance(data, dict):
            raise ValidationError("event subscription document must be an object")
        return cls(
            id=data.get("id", ""),
            workspace_id=data.get("workspace_id", ""),
            name=data.get("name", ""),
            event_type=data.get("event_type", ""),
            filter=data.get("filter") or {},
            handler_key=data.get("handler_key", ""),
            enabled=data.get("enabled", True),
            created_at=data.get("created_at", ""),
        )


def matching_subscriptions(
    subscriptions: Iterable[EventSubscription], event: dict
) -> list[EventSubscription]:
    """Enabled subscriptions whose ``event_type`` and filter match ``event``."""
    event_type = event.get("type") if isinstance(event, dict) else None
    matched: list[EventSubscription] = []
    for subscription in subscriptions:
        if not subscription.enabled or subscription.event_type != event_type:
            continue
        if subscription.matches(event):
            matched.append(subscription)
    return matched
