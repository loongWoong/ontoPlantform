"""Organization use cases: tree storage, membership and principal attributes.

The service owns the parts the domain model deliberately does not: storage
access, tenant resolution, id/clock generation and audit.  Every write goes
through the same PEP as the rest of the platform
(``security.check_action(actor, workspace_id, "security.manage")``), so a
viewer or an outsider cannot reshape the tree that feeds authorization
attributes.

Rows are stored through the platform ``Repository`` under the control-plane
kind ``org_unit``.  The tree is validated on every read/write: an inconsistent
tree (cycle, orphan, forged path, cross-tenant parent) raises instead of
yielding a wider ``org_path``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from ...domain.security.organization import (
    MAX_UNIT_NAME_LENGTH,
    OrganizationUnit,
    build_path,
    descendants_of,
    ensure_acyclic,
    normalize_members,
    org_unit_of,
)
from ...errors import NotFoundError, ValidationError

#: Document kind.  It is a control-plane kind for the governed repository: it
#: must be tenant-pinned but must not be filtered by a business row scope.
ORG_KIND = "org_unit"

AUDIT_ORG_CREATED = "security.org_created"
AUDIT_ORG_DELETED = "security.org_deleted"
AUDIT_ORG_MEMBERS_UPDATED = "security.org_members_updated"

MANAGE_ACTION = "security.manage"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str = "org") -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _make_id(factory: Callable[..., str] | None, prefix: str) -> str:
    """Call an injected id factory with or without a prefix argument."""
    if factory is None:
        return _new_id(prefix)
    try:
        value = factory(prefix)
    except TypeError:
        value = factory()
    text = str(value or "").strip()
    return text or _new_id(prefix)


class OrganizationService:
    """Create/delete/observe organization units and derive ``org_path``."""

    def __init__(self, repo: Any, clock: Callable[[], str] | None = None,
                 id_factory: Callable[..., str] | None = None,
                 security: Any = None, *, ctx: Any = None) -> None:
        self.repo = repo
        self.clock = clock or _now
        self.id_factory = id_factory
        self.security = security if security is not None \
            else getattr(ctx, "security", None)
        self.ctx = ctx

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def list_units(self, workspace_id: str) -> list[dict]:
        """Flat, path-ordered list; validates the tree before returning it."""
        units = self._load(workspace_id)
        return [self._as_dict(unit) for unit in units]

    def get_unit(self, workspace_id: str, unit_id: str) -> dict:
        units = self._load(workspace_id)
        unit = self._find(units, unit_id)
        return self._as_dict(unit)

    def tree(self, workspace_id: str) -> dict:
        """Nested roots *and* a flat parent -> children mapping.

        The console needs the nesting; scope editors need ``children`` by
        parent id (including the ``""`` key for roots).
        """
        units = self._load(workspace_id)
        nodes = {
            unit.id: self._as_dict(unit, children=[])
            for unit in units
        }
        roots: list[dict] = []
        children: dict[str, list[str]] = {}
        for unit in units:
            if unit.parent_id:
                nodes[unit.parent_id]["children"].append(nodes[unit.id])
                children.setdefault(unit.parent_id, []).append(unit.id)
            else:
                roots.append(nodes[unit.id])
                children.setdefault("", []).append(unit.id)
        return {
            "workspace_id": workspace_id,
            "count": len(units),
            "units": [self._as_dict(unit) for unit in units],
            "roots": roots,
            "children": children,
        }

    def attributes_for(self, actor: str, workspace_id: str) -> dict:
        """Principal attributes for a user: their deepest unit.

        A user who belongs to no unit gets ``{}`` (not an empty path): an empty
        ``org_path`` must never accidentally match an unset scope field.
        """
        unit = org_unit_of(self._load(workspace_id), actor)
        if unit is None:
            return {}
        return {"org_path": unit.path, "org_unit": unit.id}

    def principal_with_org(self, actor: str, workspace_id: str):
        """A ``Principal`` whose attributes carry the caller's organization."""
        security = self._require_security()
        return security.principal(
            actor, workspace_id, attributes=self.attributes_for(actor,
                                                                workspace_id))

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def create_unit(self, workspace_id: str, actor: str, name: str,
                    parent_id: str = "") -> dict:
        self._require_manage(actor, workspace_id)
        units = self._load(workspace_id)
        name = self._validate_name(name)
        parent: OrganizationUnit | None = None
        if str(parent_id or "").strip():
            parent = self._find(units, parent_id)
            if parent.workspace_id and parent.workspace_id != workspace_id:
                raise ValidationError(
                    "parent organization unit belongs to a different workspace")
        tenant_id = self._tenant_id(workspace_id)
        if parent is not None and parent.tenant_id \
                and parent.tenant_id != tenant_id:
            raise ValidationError(
                f"parent organization unit {parent.id!r} belongs to a "
                "different tenant")
        unit_id = self._allocate_id(units)
        unit = OrganizationUnit(
            id=unit_id,
            name=name,
            parent_id=parent.id if parent is not None else "",
            path=build_path(parent, unit_id),
            tenant_id=parent.tenant_id if parent is not None and parent.tenant_id
            else tenant_id,
            workspace_id=workspace_id,
            members=(),
            created_at=self.clock(),
        )
        unit.validate_parent(parent)
        self.repo.put(ORG_KIND, workspace_id, unit.id, unit.to_dict())
        self._audit(actor, workspace_id, AUDIT_ORG_CREATED, unit.id,
                    {"name": unit.name, "parent_id": unit.parent_id,
                     "path": unit.path})
        return self._as_dict(unit)

    def delete_unit(self, workspace_id: str, actor: str, unit_id: str,
                    cascade: bool = False) -> dict:
        """Delete a leaf; ``cascade`` may remove an empty subtree only.

        A unit with members is never deleted (directly or as part of a
        cascade) because deleting it would silently drop the ``org_path`` of
        people who still hold a role through it.  ``cascade`` deletes the
        deepest nodes first so the tree stays consistent throughout.
        """
        self._require_manage(actor, workspace_id)
        units = self._load(workspace_id)
        unit = self._find(units, unit_id)
        if unit.members:
            raise ValidationError(
                f"organization unit {unit.id!r} still has members")
        subtree = descendants_of(units, unit)
        if subtree and not cascade:
            raise ValidationError(
                f"organization unit {unit.id!r} has child units; pass "
                "cascade=True to delete an empty subtree")
        for descendant in subtree:
            if descendant.members:
                raise ValidationError(
                    f"organization unit {descendant.id!r} still has members")
        deleted = [item.id for item in subtree]
        for descendant in sorted(subtree, key=lambda item: item.depth(),
                                 reverse=True):
            self.repo.delete(ORG_KIND, workspace_id, descendant.id)
        self.repo.delete(ORG_KIND, workspace_id, unit.id)
        self._audit(actor, workspace_id, AUDIT_ORG_DELETED, unit.id,
                    {"name": unit.name, "path": unit.path,
                     "parent_id": unit.parent_id, "cascade": bool(cascade),
                     "deleted": [unit.id, *deleted]})
        return {"id": unit.id, "deleted": True, "cascaded": deleted}

    def set_members(self, workspace_id: str, actor: str, unit_id: str,
                    members: Iterable[str] | str | None) -> dict:
        self._require_manage(actor, workspace_id)
        units = self._load(workspace_id)
        unit = self._find(units, unit_id)
        normalized = normalize_members(members)
        before = set(unit.members)
        after = set(normalized)
        unit.members = normalized
        self.repo.put(ORG_KIND, workspace_id, unit.id, unit.to_dict())
        self._audit(actor, workspace_id, AUDIT_ORG_MEMBERS_UPDATED, unit.id,
                    {"members": list(unit.members),
                     "added": sorted(after - before),
                     "removed": sorted(before - after)})
        return self._as_dict(unit)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _load(self, workspace_id: str) -> list[OrganizationUnit]:
        if not str(workspace_id or "").strip():
            raise ValidationError("workspace id must not be empty")
        rows = self.repo.list(ORG_KIND, workspace_id)
        units = [OrganizationUnit.from_dict(row) for row in rows]
        ensure_acyclic(units)
        return sorted(units, key=lambda unit: (unit.path, unit.id))

    def _find(self, units: list[OrganizationUnit],
              unit_id: Any) -> OrganizationUnit:
        target = str(unit_id or "").strip()
        for unit in units:
            if unit.id == target:
                return unit
        raise NotFoundError(f"organization unit not found: {unit_id!r}")

    def _as_dict(self, unit: OrganizationUnit,
                 children: list[dict] | None = None) -> dict:
        data = unit.to_dict()
        data["depth"] = unit.depth()
        if children is not None:
            data["children"] = children
        return data

    def _allocate_id(self, units: list[OrganizationUnit]) -> str:
        existing = {unit.id for unit in units}
        for _ in range(50):
            candidate = _make_id(self.id_factory, "org")
            if candidate not in existing:
                return candidate
        raise ValidationError("could not allocate a unique organization unit id")

    def _tenant_id(self, workspace_id: str) -> str:
        resolver = getattr(self.security, "tenant_id", None)
        if callable(resolver):
            resolved = resolver(workspace_id)
            if resolved:
                return str(resolved)
        return workspace_id

    def _validate_name(self, name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            raise ValidationError("organization unit name must not be empty")
        if len(text) > MAX_UNIT_NAME_LENGTH:
            raise ValidationError(
                f"organization unit name must be at most "
                f"{MAX_UNIT_NAME_LENGTH} characters")
        return text

    def _require_security(self):
        if self.security is None:
            raise ValidationError(
                "an authorization kernel is required for organization "
                "management")
        return self.security

    def _require_manage(self, actor: str, workspace_id: str) -> None:
        security = self._require_security()
        check = getattr(security, "check_action", None)
        if not callable(check):
            raise ValidationError(
                "the authorization kernel cannot check security.manage")
        check(actor, workspace_id, MANAGE_ACTION)

    def _audit(self, actor: str, workspace_id: str, action: str,
               target: str, detail: Mapping[str, Any] | None = None) -> None:
        """Write a ``ctx.audit_event``-style row.

        The composition root passes ``ctx`` so the platform's own audit writer
        (tenant + append-only log) is used.  When the service is built without
        a context, fall back to the kernel's audit method so the event is not
        lost.
        """
        payload = dict(detail or {})
        if self.ctx is not None:
            writer = getattr(self.ctx, "audit_event", None)
            if callable(writer):
                writer(actor, workspace_id, action, target, payload)
                return
        kernel_writer = getattr(self.security, "_audit", None)
        if callable(kernel_writer):
            kernel_writer(actor, workspace_id, action, target, payload)
            return
        writer = getattr(self.security, "audit_event", None)
        if callable(writer):
            writer(actor, workspace_id, action, target, payload)


__all__ = [
    "AUDIT_ORG_CREATED",
    "AUDIT_ORG_DELETED",
    "AUDIT_ORG_MEMBERS_UPDATED",
    "MANAGE_ACTION",
    "ORG_KIND",
    "OrganizationService",
]
