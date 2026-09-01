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
        return data


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
        self._ctx.require(actor, workspace_id, "owner")
        if role not in ("viewer", "editor", "owner"):
            raise ValidationError(f"unknown role {role!r}")
        membership = {
            "workspace_id": workspace_id, "user": user, "role": role,
        }
        self._ctx.repo.put("membership", workspace_id, user, membership)
        self._ctx.audit_event(actor, workspace_id, "workspace.add_member", user,
                              {"role": role})
        return membership
