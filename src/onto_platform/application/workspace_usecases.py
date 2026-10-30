"""Workspace use cases."""

from __future__ import annotations

from ..domain.workspace.model import Workspace
from ..errors import AccessDeniedError, NotFoundError, ValidationError
from .common import ApplicationContext

#: Documents that do not make a workspace "non-empty".  Membership and model
#: settings are workspace plumbing; controller state and the outbox log are
#: derived/replayed infrastructure rather than definitions or business state.
NON_CONTENT_KINDS = frozenset({
    "workspace", "membership", "llm_config", "controller_state", "event",
})

#: A reset keeps the container, its people and its model configuration; every
#: other document (definitions, data, releases, runs, events) is removed.
RESET_PRESERVED_KINDS = frozenset({"workspace", "membership", "llm_config"})


def workspace_blockers(ctx: ApplicationContext, workspace_id: str) -> dict[str, int]:
    """Kinds that prevent deleting a workspace, with their document counts."""
    counts = ctx.repo.workspace_kinds(workspace_id)
    return {kind: count for kind, count in sorted(counts.items())
            if count and kind not in NON_CONTENT_KINDS}


def deletion_summary(ctx: ApplicationContext, workspace_id: str) -> dict:
    blockers = workspace_blockers(ctx, workspace_id)
    return {"deletable": not blockers, "blockers": list(blockers)}


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
            visible.append({**data, **deletion_summary(self._ctx, data["id"])})
        return visible


class DeleteWorkspace:
    """Delete an empty workspace and all of its documents.

    "Empty" means no definition or business artifact remains: ontology,
    datasource, projection, capability, application, release/deployment,
    schema snapshot, execution/agent run or todo.  Memberships, model config,
    controller state, outbox events and the append-only audit log do not make a
    workspace non-empty (the audit evidence intentionally survives).
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, confirm: bool = False) -> dict:
        self._ctx.require_action(actor, workspace_id, "workspace.delete")
        data = self._ctx.repo.get("workspace", None, workspace_id)
        if data is None:
            raise NotFoundError(f"workspace not found: {workspace_id!r}")
        if not confirm:
            raise ValidationError(
                "workspace deletion must be confirmed with {'confirm': true}")
        blockers = workspace_blockers(self._ctx, workspace_id)
        if blockers:
            detail = ", ".join(f"{kind}={count}" for kind, count in blockers.items())
            raise ValidationError(
                f"workspace {workspace_id!r} is not empty ({detail}); "
                "only empty workspaces can be deleted")
        with self._ctx.transaction():
            removed = self._ctx.repo.delete_workspace_documents(workspace_id)
            if self._ctx.repo.delete("workspace", None, workspace_id):
                removed += 1
            self._ctx.audit_event(
                actor, workspace_id, "workspace.delete", workspace_id,
                {"name": data.get("name", ""), "removed_documents": removed})
        drop = getattr(self._ctx.gateways, "drop_workspace", None)
        if callable(drop):
            drop(workspace_id)  # best-effort runtime/cache cleanup
        return {"id": workspace_id, "name": data.get("name", ""),
                "deleted": True, "removed_documents": removed}


class ResetWorkspace:
    """Empty a workspace while keeping the workspace itself, its members and
    its model configuration.

    This is the recovery path for a workspace whose ontology/data/releases are
    broken: after the reset ``workspace_blockers`` is empty, so the workspace
    can also be deleted.  Other tenants' data is never touched.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, confirm: bool = False,
                 confirm_name: str = "") -> dict:
        self._ctx.require_action(actor, workspace_id, "workspace.reset")
        data = self._ctx.repo.get("workspace", None, workspace_id)
        if data is None:
            raise NotFoundError(f"workspace not found: {workspace_id!r}")
        if not confirm:
            raise ValidationError(
                "workspace reset must be confirmed with {'confirm': true}")
        name = data.get("name", "")
        if str(confirm_name or "") != name:
            raise ValidationError(
                f"confirm_name must exactly match the workspace name {name!r}")
        kinds = self._ctx.repo.workspace_kinds(workspace_id)
        removed_kinds = sorted(
            kind for kind, count in kinds.items()
            if count and kind not in RESET_PRESERVED_KINDS)
        # Snapshot the preserved plumbing documents first: they live under the
        # workspace id and would otherwise be removed with everything else.
        preserved_members = self._ctx.repo.list("membership", workspace_id)
        preserved_llm = self._ctx.repo.get("llm_config", workspace_id, "llm")
        removed_documents = 0
        with self._ctx.transaction():
            self._ctx.repo.delete_workspace_documents(workspace_id)
            for row in preserved_members:
                self._ctx.repo.put("membership", workspace_id, row["user"], row)
            if preserved_llm is not None:
                self._ctx.repo.put("llm_config", workspace_id, "llm",
                                   preserved_llm)
            removed_documents = sum(kinds[kind] for kind in removed_kinds)
            self._ctx.audit_event(
                actor, workspace_id, "workspace.reset", workspace_id,
                {"name": name, "removed_documents": removed_documents,
                 "removed_kinds": removed_kinds})
        # drop every cached runtime ring for this workspace (best effort)
        drop = getattr(self._ctx.gateways, "drop_workspace", None)
        if callable(drop):
            drop(workspace_id)
        return {"workspace_id": workspace_id, "name": name, "reset": True,
                "removed_documents": removed_documents,
                "removed_kinds": removed_kinds}


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
