"""Tenant aggregation above the workspace boundary.

V0.1 modeled one workspace as one tenant.  This module makes the existing
``tenant_id`` column real: one tenant can own several workspaces, tenant
membership grants roles in every workspace of that tenant, and the tenant
summary endpoint aggregates only the workspaces of *that* tenant.  Workspace
resource access remains pinned by ``GovernedRepository``; aggregation is an
explicit, separately-authorized read.

Storage:

* ``tenant`` rows live under the global ``None`` workspace key, like
  ``workspace`` rows;
* ``tenant_membership`` rows are stored under the tenant id key and are
  control-plane rows (never row-filtered by a business scope).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.security.model import Tenant
from ...errors import AccessDeniedError, NotFoundError, ValidationError

TENANT_KIND = "tenant"
TENANT_MEMBERSHIP_KIND = "tenant_membership"
TENANT_ROLES = ("viewer", "editor", "owner")
_TENANT_RANK = {role: index for index, role in enumerate(TENANT_ROLES)}


@dataclass(slots=True)
class TenantService:
    ctx: Any
    security: Any

    # ---- storage helpers -------------------------------------------------

    def _raw(self):
        return getattr(self.ctx.repo, "raw", self.ctx.repo)

    def get(self, tenant_id: str) -> dict:
        row = self._raw().get(TENANT_KIND, None, tenant_id)
        if row is None:
            raise NotFoundError(f"tenant not found: {tenant_id!r}")
        return row

    def list(self, actor: str) -> list[dict]:
        visible: list[dict] = []
        for row in self._raw().list(TENANT_KIND):
            if self._is_platform_admin(actor) or self._role(actor, row["id"]):
                visible.append({**row, "role": self._role(actor, row["id"])})
        return visible

    def members(self, tenant_id: str, actor: str) -> list[dict]:
        self.require(actor, tenant_id, "viewer")
        return sorted(self._raw().list(TENANT_MEMBERSHIP_KIND, tenant_id),
                      key=lambda row: row.get("user", ""))

    def _membership(self, tenant_id: str, actor: str) -> dict | None:
        return self._raw().get(TENANT_MEMBERSHIP_KIND, tenant_id, actor)

    def _role(self, actor: str, tenant_id: str) -> str:
        row = self._membership(tenant_id, actor)
        return str(row.get("role") or "") if row else ""

    def _is_platform_admin(self, actor: str) -> bool:
        return actor in getattr(self.ctx, "platform_admins", frozenset())

    def require(self, actor: str, tenant_id: str,
                required: str = "viewer") -> str:
        if required not in _TENANT_RANK:
            raise ValidationError(f"unknown tenant role {required!r}")
        if self._is_platform_admin(actor) and required == "viewer":
            return "owner"
        role = self._role(actor, tenant_id)
        if not role:
            raise AccessDeniedError(
                f"user {actor!r} is not a member of tenant {tenant_id!r}")
        if _TENANT_RANK.get(role, -1) < _TENANT_RANK[required]:
            raise AccessDeniedError(
                f"tenant role {role!r} does not satisfy {required!r}")
        return role

    # ---- lifecycle -------------------------------------------------------

    def create(self, actor: str, name: str) -> dict:
        # Tenant creation is a platform-level act, not a workspace action.
        from ..security import context as auth_context

        auth_context.clear()
        if not str(name or "").strip():
            raise ValidationError("tenant name must not be empty")
        if getattr(self.ctx, "platform_admins", frozenset()) \
                and not self._is_platform_admin(actor):
            raise AccessDeniedError(
                "only a platform administrator can create a tenant")
        tenant = Tenant(id=self.ctx.id_factory(), name=str(name).strip(),
                        workspace_id="", boundary="tenant")
        self._raw().put(TENANT_KIND, None, tenant.id, tenant.to_dict())
        self._raw().put(TENANT_MEMBERSHIP_KIND, tenant.id, actor, {
            "tenant_id": tenant.id, "user": actor, "role": "owner",
            "created_at": self.ctx.clock(),
        })
        self.ctx.audit_event(actor, None, "tenant.create", tenant.id,
                             {"name": tenant.name})
        return tenant.to_dict()

    def add_member(self, tenant_id: str, actor: str, member: str,
                   role: str = "viewer") -> dict:
        self.require(actor, tenant_id, "owner")
        if role not in _TENANT_RANK:
            raise ValidationError(f"unknown tenant role {role!r}")
        member = str(member or "").strip()
        if not member:
            raise ValidationError("tenant member must not be empty")
        row = {"tenant_id": tenant_id, "user": member, "role": role,
               "updated_at": self.ctx.clock()}
        self._raw().put(TENANT_MEMBERSHIP_KIND, tenant_id, member, row)
        self.ctx.audit_event(actor, None, "tenant.member_added", tenant_id,
                             {"member": member, "role": role})
        return row

    def remove_member(self, tenant_id: str, actor: str, member: str) -> dict:
        self.require(actor, tenant_id, "owner")
        deleted = self._raw().delete(TENANT_MEMBERSHIP_KIND, tenant_id, member)
        if not deleted:
            raise NotFoundError(f"tenant member not found: {member!r}")
        self.ctx.audit_event(actor, None, "tenant.member_removed", tenant_id,
                             {"member": member})
        return {"tenant_id": tenant_id, "member": member, "deleted": True}

    # ---- aggregation -----------------------------------------------------

    def workspaces(self, tenant_id: str, actor: str) -> list[dict]:
        self.require(actor, tenant_id, "viewer")
        rows = [row for row in self._raw().list("workspace")
                if str(row.get("tenant_id") or row.get("id")) == tenant_id]
        return sorted(rows, key=lambda row: row.get("name", ""))

    def summary(self, tenant_id: str, actor: str) -> dict:
        """Counts per workspace, never raw rows from another tenant."""
        role = self.require(actor, tenant_id, "viewer")
        workspaces = self.workspaces(tenant_id, actor)
        items: list[dict] = []
        totals: dict[str, int] = {}
        for workspace in workspaces:
            wid = str(workspace["id"])
            kinds = self._raw().workspace_kinds(wid)
            totals["workspace"] = totals.get("workspace", 0) + 1
            for kind, count in kinds.items():
                totals[kind] = totals.get(kind, 0) + int(count)
            items.append({
                "id": wid,
                "name": workspace.get("name", ""),
                "created_at": workspace.get("created_at", ""),
                "document_counts": dict(sorted(kinds.items())),
            })
        return {
            "tenant": self.get(tenant_id),
            "role": role,
            "workspaces": items,
            "totals": dict(sorted(totals.items())),
        }

    def workspace_ids(self, tenant_id: str) -> list[str]:
        """Trusted internal helper: every workspace id in this tenant."""
        return [str(row["id"]) for row in self._raw().list("workspace")
                if str(row.get("tenant_id") or row.get("id")) == tenant_id]
