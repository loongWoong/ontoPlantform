"""Opt-in scoped repository: tenant + row scope at the storage boundary.

The platform's existing repository is workspace-keyed, so V0.1 tenant isolation
is already structural.  This wrapper adds the missing row/field enforcement for
new data paths without rewriting every use case at once:

    scope = security.authorized_scope(decision, principal, tenant)
    repo = ScopedRepository(ctx.repo, scope)
    rows = repo.list("vehicle_pass", workspace_id)

Every row is forced through the decision's ``row_filter`` and the writes are
pinned to the decision's tenant/workspace; a caller cannot pass another
tenant id in the payload.  Reads that are not allowed return an empty list (a
404/empty result at the API), never a cross-tenant row.
"""

from __future__ import annotations

from typing import Any

from ...errors import AccessDeniedError


class ScopedRepository:
    def __init__(self, repo: Any, scope: Any) -> None:
        self._repo = repo
        self.scope = scope

    # ---- tenant pinning ---------------------------------------------------

    @property
    def decision(self):
        return self.scope.decision

    @property
    def workspace_id(self) -> str:
        return self.scope.tenant.workspace_id

    @property
    def tenant_id(self) -> str:
        return self.scope.tenant.tenant_id

    def _pin(self, workspace_id: str | None) -> str:
        if workspace_id not in (None, "", self.workspace_id):
            raise AccessDeniedError(
                "cross-tenant repository access is denied")
        return self.workspace_id

    def _enrich(self, row: dict, workspace_id: str) -> dict:
        return {**row,
                "tenant_id": row.get("tenant_id") or self.tenant_id,
                "workspace_id": row.get("workspace_id") or workspace_id}

    def _authorized(self, rows: list[dict], workspace_id: str) -> list[dict]:
        prepared = [self._enrich(row, workspace_id) for row in rows]
        filtered = self.scope.filter(prepared)
        return self.scope.mask(filtered)

    # ---- Repository subset used by new data paths -------------------------

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None:
        wid = self._pin(workspace_id)
        row = self._repo.get(kind, wid, entity_id)
        if row is None:
            return None
        prepared = self._enrich(row, wid)
        if not self.scope.filter([prepared]):
            return None
        return self.scope.mask([prepared])[0]

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        wid = self._pin(workspace_id)
        rows = [row for row in self._repo.list(kind, wid)
                if not row.get("workspace_id")
                or row.get("workspace_id") == wid]
        return self._authorized(rows, wid)

    def put(self, kind: str, workspace_id: str | None, entity_id: str,
            data: dict) -> int:
        wid = self._pin(workspace_id)
        payload = dict(data)
        payload["tenant_id"] = self.tenant_id
        payload["workspace_id"] = wid
        # A write must satisfy the same row scope as a read; otherwise a
        # caller with a narrow scope could create/overwrite rows outside it.
        if not self.scope.filter([payload]):
            raise AccessDeniedError(
                "write is outside the authorized row scope")
        return self._repo.put(kind, wid, entity_id, payload)

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool:
        wid = self._pin(workspace_id)
        # Read the raw row here: a filtered outer read would turn "out of
        # scope" into "not found" and silently skip the explicit deny.
        base = getattr(self._repo, "raw", self._repo)
        row = base.get(kind, wid, entity_id)
        if row is None:
            return False
        if not self.scope.filter([self._enrich(row, wid)]):
            raise AccessDeniedError(
                "delete is outside the authorized row scope")
        return base.delete(kind, wid, entity_id)

    def __getattr__(self, name: str) -> Any:
        # transactions, versions, kinds and workspace deletion delegate; new
        # data paths that need them should still pass an authorized scope.
        return getattr(self._repo, name)


__all__ = ["ScopedRepository", "GovernedRepository"]


#: Documents that are security/control-plane facts rather than workspace
#: business data.  They are still workspace-pinned but must not be filtered by
#: a business row scope such as ``station = principal.station``.
CONTROL_KINDS = frozenset({
    "workspace", "membership", "llm_config", "security_policy",
    "role_binding", "security_group", "relation_tuple",
    "authorization_decision", "audit", "event", "org_unit",
    "identity_user", "webhook", "scheduler_task",
})


class GovernedRepository:
    """Repository boundary installed as ``ctx.repo``.

    Every call made while an authorization context is active is:
      * pinned to the decision's workspace (cross-workspace access is a deny),
      * forced through the decision's row scope for non-control kinds,
      * field-masked on reads and scope-checked on writes.

    Calls from system code (scheduler start-up, migrations) have no active
    context and pass through unchanged.  Control-plane kinds bypass row
    filtering but never tenant pinning.
    """

    def __init__(self, repo: Any, scope_provider=None,
                 control_kinds: frozenset[str] = CONTROL_KINDS) -> None:
        self._repo = repo
        self._scope_provider = scope_provider
        self._control = control_kinds

    def _scope(self):
        if self._scope_provider is None:
            return None
        try:
            return self._scope_provider()
        except Exception:
            return None

    @property
    def raw(self) -> Any:
        """The unguarded repository (fixtures/migrations only)."""
        return self._repo

    def _pin(self, kind: str, workspace_id: str | None, scope) -> str | None:
        if scope is None:
            return workspace_id
        current = scope.tenant.workspace_id
        if kind == "workspace":
            # Workspace documents are stored under the ``None`` workspace key
            # (``repo.put("workspace", None, id, ...)``); the row's own id is
            # the tenant.  Keep that storage convention but validate the id.
            if workspace_id not in (None, "", current):
                raise AccessDeniedError(
                    "cross-tenant repository access is denied")
            return None
        if kind in self._control:
            # Control-plane rows skip row/field filters, never tenant pinning:
            # an active decision for workspace A must not read or write
            # workspace B's memberships, decisions, audit or identity rows.
            if workspace_id not in (None, "") and workspace_id != current:
                raise AccessDeniedError(
                    "cross-tenant repository access is denied")
            return current
        if workspace_id not in (None, "", current):
            raise AccessDeniedError(
                "cross-tenant repository access is denied")
        return current

    @staticmethod
    def _enrich(row: dict, scope) -> dict:
        return {**row,
                "tenant_id": row.get("tenant_id") or scope.tenant.tenant_id,
                "workspace_id": row.get("workspace_id") or scope.tenant.workspace_id}

    def _reads(self, kind: str, rows: list[dict], scope) -> list[dict]:
        if scope is None:
            return rows
        prepared = [self._enrich(row, scope) for row in rows]
        if kind not in self._control:
            prepared = scope.filter(prepared)
        return scope.mask(prepared)

    def get(self, kind: str, workspace_id: str | None,
            entity_id: str) -> dict | None:
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope)
        row = self._repo.get(kind, wid, entity_id)
        if row is None:
            return None
        if scope is None:
            return row
        rows = self._reads(kind, [row], scope)
        return rows[0] if rows else None

    def get_versioned(self, kind: str, workspace_id: str | None,
                      entity_id: str):
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope)
        got = self._repo.get_versioned(kind, wid, entity_id)
        if got is None:
            return None
        row, version = got
        if scope is None:
            return row, version
        rows = self._reads(kind, [row], scope)
        return (rows[0] if rows else None), version

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        scope = self._scope()
        if scope is not None and kind == "workspace":
            current = scope.tenant.workspace_id
            if workspace_id not in (None, "", current):
                raise AccessDeniedError(
                    "cross-tenant repository access is denied")
            # Workspace rows are stored under the ``None`` key; a bound
            # request may only list its own workspace, never every tenant.
            row = self._repo.get("workspace", None, current)
            return [row] if row is not None else []
        wid = self._pin(kind, workspace_id, scope)
        rows = self._repo.list(kind, wid)
        if scope is None:
            return rows
        if kind in self._control:
            return rows
        rows = [row for row in rows
                if not row.get("workspace_id")
                or row.get("workspace_id") == scope.tenant.workspace_id]
        return self._reads(kind, rows, scope)

    def _prepare_write(self, kind: str, workspace_id: str | None,
                       data: dict, scope) -> tuple[str | None, dict]:
        wid = self._pin(kind, workspace_id, scope)
        if scope is None:
            return wid, data
        payload = dict(data)
        payload["tenant_id"] = scope.tenant.tenant_id
        payload["workspace_id"] = scope.tenant.workspace_id
        if kind not in self._control and not scope.filter([payload]):
            raise AccessDeniedError(
                "write is outside the authorized row scope")
        return wid, payload

    def put(self, kind: str, workspace_id: str | None, entity_id: str,
            data: dict) -> int:
        scope = self._scope()
        wid, payload = self._prepare_write(kind, workspace_id, data, scope)
        return self._repo.put(kind, wid, entity_id, payload)

    def put_if_version(self, kind: str, workspace_id: str | None,
                       entity_id: str, data: dict,
                       expected_version: int | None) -> int:
        scope = self._scope()
        wid, payload = self._prepare_write(kind, workspace_id, data, scope)
        return self._repo.put_if_version(kind, wid, entity_id, payload,
                                         expected_version)

    def delete(self, kind: str, workspace_id: str | None,
               entity_id: str) -> bool:
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope)
        if scope is not None and kind not in self._control:
            row = self._repo.get(kind, wid, entity_id)
            if row is None:
                return False
            if not scope.filter([self._enrich(row, scope)]):
                raise AccessDeniedError(
                    "delete is outside the authorized row scope")
        return self._repo.delete(kind, wid, entity_id)

    def workspace_kinds(self, workspace_id: str) -> dict[str, int]:
        scope = self._scope()
        if scope is not None and workspace_id != scope.tenant.workspace_id:
            raise AccessDeniedError(
                "cross-tenant repository access is denied")
        return self._repo.workspace_kinds(workspace_id)

    def delete_workspace_documents(self, workspace_id: str) -> int:
        scope = self._scope()
        if scope is not None and workspace_id != scope.tenant.workspace_id:
            raise AccessDeniedError(
                "cross-tenant repository access is denied")
        return self._repo.delete_workspace_documents(workspace_id)

    def transaction(self):
        return self._repo.transaction()

    def close(self) -> None:
        closer = getattr(self._repo, "close", None)
        if callable(closer):
            closer()



