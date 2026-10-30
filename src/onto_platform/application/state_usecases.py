"""Semantic state maintenance: operator-driven instance delete/update.

Projections own bulk state materialization; these use cases are the narrow
*manual* door for an operator (or the console) to correct or remove one
instance object.  They deliberately reuse the runtime gateway's validated
write path (``apply_delta`` / ``delete_objects``) so kernel constraints
(types, enums, required, unique, identity, state machine, cardinality) are
enforced exactly as for any other writer.

Writes are audited as ``state.objects.delete`` / ``state.object.update`` and
labelled ``source="manual"`` so the change log distinguishes them from
projection and capability writes.
"""

from __future__ import annotations

from ..errors import NotFoundError, ValidationError
from ..ports.runtime import ObjectUpsert, SemanticDelta
from .common import ApplicationContext


def _split_ref(ref: str) -> tuple[str, str]:
    if not isinstance(ref, str) or "/" not in ref:
        raise ValidationError(
            f"object reference must be 'Type/Id', got {ref!r}")
    otype, _, oid = ref.partition("/")
    if not otype or not oid:
        raise ValidationError(
            f"object reference must be 'Type/Id', got {ref!r}")
    return otype, oid


class StateUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    def delete_objects(self, workspace_id: str, actor: str,
                       refs: list[str]) -> dict:
        """Delete object instances (and their relations) in one transaction.

        Only existing references are deleted; absent ones are reported in
        ``missing`` instead of raising, so a batch delete is idempotent from
        the caller's point of view.  The HTTP single-ref route turns a
        missing reference into 404.
        """
        self._ctx.require(actor, workspace_id, "editor")
        if not refs:
            raise ValidationError("at least one object reference is required")
        gateway = self._ctx.gateway_for(workspace_id)
        existing: list[str] = []
        missing: list[str] = []
        for ref in refs:
            _split_ref(ref)
            (existing if gateway.get_object(ref) is not None
             else missing).append(ref)
        changes: list[dict] = []
        if existing:
            changes = gateway.delete_objects(existing, source="manual")
        self._ctx.audit_event(
            actor, workspace_id, "state.objects.delete",
            ", ".join(existing) if existing else ", ".join(refs),
            {"deleted": existing, "missing": missing})
        return {"deleted": existing, "missing": missing, "changes": changes}

    # ------------------------------------------------------------------
    # update
    # ------------------------------------------------------------------

    def update_object(self, workspace_id: str, actor: str, ref: str,
                      properties: dict) -> dict:
        """Correct instance properties through the validated mutation door.

        Absent objects are a 404 (update is not an implicit create).  Identity
        attributes are immutable once an object exists, so a write that names
        one is refused before the kernel sees it.
        """
        self._ctx.require(actor, workspace_id, "editor")
        otype, oid = _split_ref(ref)
        if not isinstance(properties, dict):
            raise ValidationError(
                "properties must be an object mapping property names to values")
        gateway = self._ctx.gateway_for(workspace_id)
        current = gateway.get_object(ref)
        if current is None:
            raise NotFoundError(f"object not found: {ref}")
        try:
            descriptor = gateway.describe_object(otype)
        except Exception as exc:
            raise ValidationError(f"unknown object type: {otype!r}") from exc
        identity = set(descriptor.get("identity") or [])
        protected = sorted(set(properties) & identity)
        if protected:
            raise ValidationError(
                f"identity properties of {otype} are immutable: "
                f"{', '.join(protected)}")
        before = current.get("properties") or {}
        gateway.apply_delta(
            SemanticDelta(upserts=[ObjectUpsert(otype, oid, dict(properties))]),
            source="manual", must_exist=True)
        after = gateway.get_object(ref) or {}
        updated = after.get("properties") or {}
        changed = sorted(key for key in properties
                         if before.get(key) != updated.get(key))
        self._ctx.audit_event(
            actor, workspace_id, "state.object.update", ref,
            {"changed": changed, "properties": dict(properties)})
        return {"ref": ref, "object": after, "changed": changed}
