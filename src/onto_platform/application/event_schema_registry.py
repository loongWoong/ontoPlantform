"""Event Schema Registry (P2.6): versioned payload schemas + compatibility.

Business event contracts already carry a payload schema (``event_contract``,
see :mod:`onto_platform.application.event_usecases`), but a contract document
holds exactly one current version.  This registry adds an append-only,
workspace-scoped record of *every* published payload schema and refuses a new
version that would break consumers according to an explicit compatibility
mode.

Design notes
------------
* Rows are ordinary workspace documents (kind ``event_schema``) keyed by a
  deterministic id ``es_<event_type>_v<version>``, so a repeated registration
  is detectable without a scan and the directory listing is stable.
* Schemas are validated with the same public domain helper the contracts use
  (:func:`~onto_platform.domain.event.model.validate_payload_schema`), so the
  registry cannot drift from contract validation.  No private underscore
  helpers are imported.
* ``register`` never overwrites an existing ``(event_type, version)``: it
  raises ``ValidationError``.  Silent replacement would make the version
  number a lie for every consumer that already read it.
* Compatibility is deliberately conservative and evaluated against the
  immediately preceding version (highest version below the new one):

  - ``backward``: new schema can read data written with the old schema.
    Breaking: removed field, changed type, optional -> required, or a *new*
    required field.  New optional fields are allowed.
  - ``forward``: old schema can read data written with the new schema.
    Breaking: added field, changed type, required -> optional, or removing a
    required field (symmetric of backward).  Removing an optional field is
    allowed.
  - ``full``: both directions must hold (union of the two checks).
  - ``none``: no check (explicit opt-out for breaking versions).

The module is application-layer only: it uses ``ctx.repo``, ``ctx.clock``,
``ctx.audit_event`` and ``ctx.require`` and performs no I/O of its own.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ..domain.event import validate_event_name, validate_payload_schema
from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext

EVENT_SCHEMA_KIND = "event_schema"
COMPATIBILITY_MODES = ("backward", "forward", "full", "none")


def _new_id(ctx: ApplicationContext, prefix: str) -> str:
    """Generate a document id, tolerating alternate ``id_factory`` contracts."""
    factory = getattr(ctx, "id_factory", None)
    if callable(factory):
        try:
            return f"{prefix}_{factory()}"
        except TypeError:  # pragma: no cover - alternate factory contract
            pass
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex[:12]}"


def _validate_version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError(
            f"schema version must be an integer >= 1, got {value!r}")
    return int(value)


def _validate_mode(value: Any) -> str:
    mode = str(value or "backward").strip().lower()
    if mode not in COMPATIBILITY_MODES:
        raise ValidationError(
            f"compatibility {value!r} is invalid: "
            f"expected one of {list(COMPATIBILITY_MODES)}")
    return mode


def _schema_id(event_type: str, version: int) -> str:
    return f"es_{event_type}_v{int(version)}"


def _diff(previous: Mapping[str, Mapping[str, Any]],
          candidate: Mapping[str, Mapping[str, Any]]) -> dict:
    """Structural diff between two normalized schemas."""
    previous_fields = set(previous)
    candidate_fields = set(candidate)
    return {
        "added": sorted(candidate_fields - previous_fields),
        "removed": sorted(previous_fields - candidate_fields),
        "typeChanged": sorted(
            field for field in previous_fields & candidate_fields
            if previous[field].get("type") != candidate[field].get("type")),
    }


def breaking_changes(previous: Mapping[str, Mapping[str, Any]],
                     candidate: Mapping[str, Mapping[str, Any]],
                     mode: str = "backward") -> list[dict]:
    """Return every ``{field, reason}`` that breaks ``mode`` (empty = safe).

    ``previous``/``candidate`` must be normalized schemas (as returned by
    ``validate_payload_schema``).  ``none`` never reports a breaking change.
    """
    mode = _validate_mode(mode)
    if mode == "none":
        return []
    diff = _diff(previous, candidate)
    previous_fields = set(previous)
    candidate_fields = set(candidate)
    breaking: list[tuple[str, str]] = []

    def add(field: str, reason: str) -> None:
        entry = (field, reason)
        if entry not in breaking:
            breaking.append(entry)

    if mode in ("backward", "full"):
        for field in diff["removed"]:
            add(field, "removed_field")
        for field in diff["typeChanged"]:
            add(field, "type_changed")
        for field in sorted(previous_fields & candidate_fields):
            if not previous[field].get("required", False) \
                    and candidate[field].get("required", False):
                add(field, "required_added")
        for field in diff["added"]:
            if candidate[field].get("required", False):
                add(field, "new_required_field")

    if mode in ("forward", "full"):
        for field in diff["added"]:
            add(field, "added_field")
        for field in diff["typeChanged"]:
            add(field, "type_changed")
        for field in sorted(previous_fields & candidate_fields):
            if previous[field].get("required", False) \
                    and not candidate[field].get("required", False):
                add(field, "required_removed")
        for field in diff["removed"]:
            if previous[field].get("required", False):
                add(field, "removed_required_field")

    return [{"field": field, "reason": reason} for field, reason in breaking]


class SchemaRegistry:
    """Append-only payload schema versions for one platform's workspaces."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self.ctx = ctx

    # ---- storage helpers --------------------------------------------------

    def _rows(self, workspace_id: str, event_type: str | None = None
              ) -> list[dict]:
        rows = [deepcopy(dict(row)) for row in
                self.ctx.repo.list(EVENT_SCHEMA_KIND, workspace_id)]
        if event_type is not None:
            rows = [row for row in rows if row.get("event_type") == event_type]
        rows.sort(key=lambda row: (str(row.get("event_type") or ""),
                                   int(row.get("version") or 0)))
        return rows

    def _latest(self, workspace_id: str, event_type: str) -> dict | None:
        rows = self._rows(workspace_id, event_type)
        return rows[-1] if rows else None

    # ---- registration -----------------------------------------------------

    def register(self, workspace_id: str, actor: str, event_type: str,
                 version: int, payload_schema: dict, *,
                 description: str = "",
                 compatibility: str = "backward") -> dict:
        """Register one schema version after a compatibility check.

        An existing ``(event_type, version)`` is rejected: versions are
        immutable, so callers must pick the next number instead of silently
        redefining a version that consumers already resolved.
        """
        self.ctx.require(actor, workspace_id, "editor")
        validate_event_name(event_type)
        version = _validate_version(version)
        mode = _validate_mode(compatibility)
        if not isinstance(description, str):
            raise ValidationError("schema description must be a string")
        normalized = validate_payload_schema(payload_schema)
        row_id = _schema_id(event_type, version)
        if self.ctx.repo.get(EVENT_SCHEMA_KIND, workspace_id, row_id) \
                is not None:
            raise ValidationError(
                f"event schema {event_type!r} version {version} is already "
                "registered; versions are immutable")

        previous_rows = [row for row in self._rows(workspace_id, event_type)
                         if int(row.get("version") or 0) < version]
        if previous_rows:
            previous = previous_rows[-1]
            previous_schema = validate_payload_schema(
                previous.get("payload_schema") or {})
            breaking = breaking_changes(previous_schema, normalized, mode)
            if breaking:
                summary = ", ".join(
                    f"{item['field']} ({item['reason']})"
                    for item in breaking)
                raise ValidationError(
                    f"schema {event_type!r} v{version} is not "
                    f"{mode}-compatible with v{previous['version']}: {summary}")

        now = self.ctx.clock()
        row = {
            "id": row_id,
            "workspace_id": workspace_id,
            "event_type": event_type,
            "version": version,
            "payload_schema": normalized,
            "compatibility": mode,
            "description": description,
            "created_by": actor,
            "created_at": now,
        }
        self.ctx.save(EVENT_SCHEMA_KIND, row)
        self.ctx.audit_event(
            actor, workspace_id, "event_schema.registered", row_id,
            {"event_type": event_type, "version": version,
             "compatibility": mode,
             "fields": sorted(normalized)})
        return deepcopy(row)

    # ---- reads ------------------------------------------------------------

    def list(self, workspace_id: str, actor: str,
             event_type: str | None = None) -> list[dict]:
        """List schema versions (viewer), oldest/lowest version first."""
        self.ctx.require(actor, workspace_id, "viewer")
        if event_type is not None:
            validate_event_name(event_type)
        return self._rows(workspace_id, event_type)

    def get(self, workspace_id: str, actor: str, event_type: str,
            version: int | None = None) -> dict:
        """Return one version, or the highest registered version."""
        self.ctx.require(actor, workspace_id, "viewer")
        validate_event_name(event_type)
        if version is not None:
            version = _validate_version(version)
        rows = self._rows(workspace_id, event_type)
        if version is None:
            if not rows:
                raise NotFoundError(
                    f"no event schemas registered for {event_type!r}")
            return rows[-1]
        match = [row for row in rows if int(row.get("version") or 0) == version]
        if not match:
            raise NotFoundError(
                f"event schema {event_type!r} v{version} not found")
        return match[0]

    # ---- compatibility dry-run -------------------------------------------

    def compatibility(self, workspace_id: str, actor: str, event_type: str,
                      payload_schema: dict,
                      from_version: int | None = None) -> dict:
        """Check a candidate schema against a stored version; writes nothing.

        Returns ``{compatible, mode, breaking, added, removed, typeChanged}``.
        The check is always *backward* (the registry's default promise to
        existing consumers); ``from_version`` selects the baseline and
        defaults to the highest registered version.
        """
        self.ctx.require(actor, workspace_id, "viewer")
        validate_event_name(event_type)
        candidate = validate_payload_schema(payload_schema)
        base = self.get(workspace_id, actor, event_type, from_version) \
            if from_version is not None else self._latest(workspace_id,
                                                          event_type)
        if base is None:
            result = {"compatible": True, "mode": "backward",
                      "breaking": [], "added": sorted(candidate),
                      "removed": [], "typeChanged": []}
        else:
            previous = validate_payload_schema(base.get("payload_schema") or {})
            diff = _diff(previous, candidate)
            breaking = breaking_changes(previous, candidate, "backward")
            result = {"compatible": not breaking, "mode": "backward",
                      "breaking": breaking, **diff}
        self.ctx.audit_event(
            actor, workspace_id, "event_schema.checked",
            _schema_id(event_type, int(base.get("version") or 0))
            if base is not None else event_type,
            {"event_type": event_type,
             "from_version": None if base is None else base.get("version"),
             "compatible": result["compatible"]})
        return result


__all__ = [
    "COMPATIBILITY_MODES",
    "EVENT_SCHEMA_KIND",
    "SchemaRegistry",
    "breaking_changes",
]
