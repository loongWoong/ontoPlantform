"""Workspace use cases."""

from __future__ import annotations

from ..domain.workspace.model import Workspace
from ..errors import AccessDeniedError, ValidationError
from .common import ApplicationContext


class CreateWorkspace:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, name: str, actor: str, description: str = "") -> dict:
        if not name.strip():
            raise ValidationError("workspace name must not be empty")
        from .limits import check_workspace_quota

        check_workspace_quota(self._ctx.max_workspaces,
                              len(self._ctx.repo.list("workspace")))
        workspace = Workspace(
            id=self._ctx.id_factory(),
            name=name.strip(),
            description=description,
            created_at=self._ctx.clock(),
            runtime_config=dict(self._ctx.default_runtime_config),
        )
        self._ctx.repo.put("workspace", None, workspace.id, workspace.to_dict())
        # the creator becomes the workspace owner (design.txt §29 Access)
        self._ctx.repo.put(
            "membership",
            workspace.id,
            actor,
            {
                "workspace_id": workspace.id,
                "user": actor,
                "role": "owner",
            },
        )
        self._ctx.audit_event(actor, workspace.id, "workspace.create", workspace.id,
                              {"name": workspace.name})
        return workspace.to_dict()


class GetWorkspace:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        data = self._ctx.repo.get("workspace", None, workspace_id)
        if data is None:
            from ..errors import NotFoundError

            raise NotFoundError(f"workspace not found: {workspace_id!r}")
        members = {
            m["user"]: m["role"]
            for m in self._ctx.repo.list("membership", workspace_id)
        }
        return {**data, "members": members}


class ListWorkspaces:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, actor: str) -> list[dict]:
        """Only workspaces the actor belongs to."""
        visible = []
        for data in self._ctx.repo.list("workspace"):
            try:
                self._ctx.require(actor, data["id"], "viewer")
            except AccessDeniedError:
                continue
            visible.append(data)
        return visible


class AddMember:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, user: str, role: str, actor: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "workspace.manage_members")
        if role not in ("viewer", "editor", "owner"):
            raise ValidationError(f"unknown role {role!r}")
        self._guard_last_owner(workspace_id, user, role)
        membership = {
            "workspace_id": workspace_id, "user": user, "role": role,
        }
        self._ctx.repo.put("membership", workspace_id, user, membership)
        self._ctx.audit_event(actor, workspace_id, "workspace.add_member", user,
                              {"role": role})
        return membership

    def _guard_last_owner(self, workspace_id: str, user: str, role: str) -> None:
        """A workspace must always keep at least one owner (plan-v2 1.8)."""
        members = self._ctx.repo.list("membership", workspace_id)
        current = {m["user"]: m["role"] for m in members}
        if current.get(user) == "owner" and role != "owner":
            owners = [u for u, r in current.items() if r == "owner"]
            if owners == [user]:
                raise ValidationError(
                    f"{user!r} is the last owner of workspace {workspace_id!r}; "
                    "promote another owner before changing this membership")


class RemoveMember:
    """Delete a membership; the last owner can never be removed."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, user: str, actor: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "workspace.manage_members")
        current = {m["user"]: m["role"]
                   for m in self._ctx.repo.list("membership", workspace_id)}
        if user not in current:
            from ..errors import NotFoundError

            raise NotFoundError(f"user {user!r} is not a member of {workspace_id!r}")
        if current[user] == "owner" and \
                [u for u, r in current.items() if r == "owner"] == [user]:
            raise ValidationError(
                f"{user!r} is the last owner of workspace {workspace_id!r}; "
                "promote another owner before removing this membership")
        self._ctx.repo.delete("membership", workspace_id, user)
        self._ctx.audit_event(actor, workspace_id, "workspace.remove_member",
                              user, {"previous_role": current[user]})
        return {"workspace_id": workspace_id, "user": user,
                "role": None, "removed": True}


class ListMembers:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("membership", workspace_id)
