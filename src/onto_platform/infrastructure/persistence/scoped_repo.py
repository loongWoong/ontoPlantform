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
from ...domain.security.filters import (
    compile_filter_sql,
    filter_rows,
    normalize_filter,
)


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
    "workspace", "membership", "tenant", "tenant_membership",
    "llm_config", "security_policy", "role_binding", "security_group",
    "relation_tuple", "authorization_decision", "audit", "event",
    "org_unit", "identity_user", "webhook", "scheduler_task",
    "async_task", "break_glass", "mfa_enrollment",
})


class GovernedRepository:
    """Repository boundary installed as ``ctx.repo``.

    Every call made while an authorization context is active is:
      * pinned to the decision's workspace (cross-workspace access is a deny),
      * forced through the decision's row scope for non-control kinds,
      * field-masked on reads and scope-checked on writes.

    P2.2 hard tenant boundary (L1).  Workspace equality alone is a logical
    pin: a forged decision (tenant B bound to workspace A) would still pass it.
    Under an active scope this wrapper therefore also verifies tenant
    consistency at the storage boundary:

      * business documents are only reachable when the pinned workspace
        actually belongs to ``scope.tenant``;
      * ``workspace`` documents (stored under the ``None`` key by id) may only
        be the bound request's own row, and that row's ``tenant_id`` (falling
        back to its ``id``) must match the decision's tenant;
      * rows carrying an explicit foreign ``tenant_id`` are treated as out of
        scope (filtered on list, denied on get/delete).

    The workspace -> tenant mapping is resolved from the workspace row via the
    injected ``tenant_resolver`` (default: a raw-repository lookup), so this
    module never imports the application/security layer.

    Calls from system code (scheduler start-up, migrations) have no active
    context and pass through unchanged.  Control-plane kinds bypass row
    filtering but never workspace/tenant pinning.
    """

    def __init__(self, repo: Any, scope_provider=None,
                 control_kinds: frozenset[str] = CONTROL_KINDS, *,
                 tenant_resolver=None) -> None:
        self._repo = repo
        self._scope_provider = scope_provider
        self._control = control_kinds
        self._tenant_resolver = tenant_resolver

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

    # ---- tenant consistency -------------------------------------------------

    @staticmethod
    def _deny() -> None:
        raise AccessDeniedError("cross-tenant repository access is denied")

    def _store(self) -> Any:
        # The unguarded repository: resolving a workspace's tenant must never
        # recurse back through this governed wrapper.
        return getattr(self._repo, "raw", self._repo)

    def _workspace_tenant(self, workspace_id: str) -> str | None:
        """Which tenant owns ``workspace_id`` (raw lookup, no recursion)."""
        if self._tenant_resolver is not None:
            try:
                resolved = self._tenant_resolver(workspace_id)
            except Exception:
                return None
            return str(resolved) if resolved else None
        row = self._store().get("workspace", None, workspace_id)
        if not isinstance(row, dict):
            return None
        return str(row.get("tenant_id") or row.get("id") or "") or None

    @staticmethod
    def _workspace_row_tenant(row: dict, workspace_id: str) -> str:
        """A workspace row's tenant, V0.1 fallback: the workspace id itself."""
        return str(row.get("tenant_id") or row.get("id") or workspace_id or "")

    def _assert_tenant(self, actual: object, scope) -> None:
        if str(actual or "") != str(scope.tenant.tenant_id):
            self._deny()

    def _assert_workspace_row(self, row: dict, workspace_id: str, scope) -> None:
        self._assert_tenant(self._workspace_row_tenant(row, workspace_id),
                            scope)

    @staticmethod
    def _row_tenant_ok(row: dict, scope) -> bool:
        """Rows without ``tenant_id`` predate the column and are workspace-pinned.

        An explicit foreign tenant is never our data, even if the row was
        stored under our workspace key.
        """
        actual = row.get("tenant_id")
        return not actual or str(actual) == str(scope.tenant.tenant_id)

    # ---- pinning ------------------------------------------------------------

    def _pin(self, kind: str, workspace_id: str | None, scope,
             entity_id: str | None = None) -> str | None:
        if scope is None:
            return workspace_id
        current = scope.tenant.workspace_id
        if kind == "workspace":
            # Workspace documents are stored under the ``None`` workspace key
            # (``repo.put("workspace", None, id, ...)``); the row's own id is
            # the tenant.  Keep that storage convention but validate the id:
            # a bound request may only touch its own row, never another
            # tenant's workspace document at the same key.
            if workspace_id not in (None, "", current):
                self._deny()
            if entity_id is not None and str(entity_id) != str(current):
                self._deny()
            return None
        if kind in self._control:
            # Control-plane rows skip row/field filters, never workspace
            # pinning: an active decision for workspace A must not read or
            # write workspace B's memberships, decisions, audit or identity
            # rows.
            if workspace_id not in (None, "") and workspace_id != current:
                self._deny()
            return current
        if workspace_id not in (None, "", current):
            self._deny()
        # Workspace equality is necessary but not sufficient: the pinned
        # workspace must belong to the decision's tenant, so a forged scope
        # (tenant B + workspace A) cannot reach A's business documents.
        self._assert_tenant(self._workspace_tenant(current), scope)
        return current

    @staticmethod
    def _enrich(row: dict, scope) -> dict:
        return {**row,
                "tenant_id": row.get("tenant_id") or scope.tenant.tenant_id,
                "workspace_id": row.get("workspace_id") or scope.tenant.workspace_id}

    def _reads(self, kind: str, rows: list[dict], scope) -> list[dict]:
        if scope is None:
            return rows
        if kind not in self._control:
            # Foreign-tenant rows are not "our workspace data" even when they
            # were written under the same storage key.
            rows = [row for row in rows if self._row_tenant_ok(row, scope)]
        prepared = [self._enrich(row, scope) for row in rows]
        if kind not in self._control:
            prepared = scope.filter(prepared)
        return scope.mask(prepared)

    def get(self, kind: str, workspace_id: str | None,
            entity_id: str) -> dict | None:
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope, entity_id)
        row = self._repo.get(kind, wid, entity_id)
        if row is None:
            return None
        if scope is None:
            return row
        if kind == "workspace":
            self._assert_workspace_row(row, str(entity_id), scope)
        elif kind not in self._control and not self._row_tenant_ok(row, scope):
            self._deny()
        rows = self._reads(kind, [row], scope)
        return rows[0] if rows else None

    def get_versioned(self, kind: str, workspace_id: str | None,
                      entity_id: str):
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope, entity_id)
        got = self._repo.get_versioned(kind, wid, entity_id)
        if got is None:
            return None
        row, version = got
        if scope is None:
            return row, version
        if kind == "workspace":
            self._assert_workspace_row(row, str(entity_id), scope)
        elif kind not in self._control and not self._row_tenant_ok(row, scope):
            self._deny()
        rows = self._reads(kind, [row], scope)
        if not rows:
            # An out-of-scope row must not leak even its existence/version.
            return None
        return rows[0], version

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        scope = self._scope()
        if scope is not None and kind == "workspace":
            current = scope.tenant.workspace_id
            if workspace_id not in (None, "", current):
                self._deny()
            # Workspace rows are stored under the ``None`` key; a bound
            # request may only list its own workspace, never every tenant.
            row = self._repo.get("workspace", None, current)
            if row is None:
                return []
            # The bound scope is only valid while the workspace still belongs
            # to that tenant (tamper/re-parenting changes invalidate it).
            self._assert_workspace_row(row, current, scope)
            return [row]
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

    def query(self, kind: str, workspace_id: str | None = None, *,
              filter: dict | None = None, limit: int | None = None) -> list[dict]:
        """Authorized query with the row scope pushed into SQL.

        The scope AST and the caller's filter are merged before compilation, so
        the database applies exactly the predicate the PDP previewed.  Memory
        repositories fall back to the same predicate evaluated in Python.
        """
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope)
        node = None
        if filter is not None:
            node = normalize_filter(filter)
        if scope is not None and scope.decision.scope:
            node = ({"all": [scope.decision.scope, node]}
                    if node is not None else scope.decision.scope)
        if node is None:
            rows = self._repo.list(kind, wid)
        elif hasattr(self._repo, "query_sql"):
            where, params = compile_filter_sql(
                node,
                scope.principal if scope is not None else None,
                scope.tenant.to_dict() if scope is not None else {},
                columns={"tenant_id": "tenant_id",
                         "workspace_id": "workspace_id"},
                json_data_column="data")
            rows = self._repo.query_sql(kind, wid, where, params, limit)
        else:
            principal = scope.principal if scope is not None else None
            context = scope.tenant.to_dict() if scope is not None else {}
            rows = filter_rows(self._repo.list(kind, wid), node, principal,
                               context)
            if limit is not None:
                rows = rows[:max(0, int(limit))]
        if scope is None:
            return rows
        return self._reads(kind, rows, scope)

    def _prepare_write(self, kind: str, workspace_id: str | None,
                       entity_id: str, data: dict,
                       scope) -> tuple[str | None, dict]:
        wid = self._pin(kind, workspace_id, scope, entity_id)
        if scope is None:
            return wid, data
        if kind == "workspace":
            # Workspace rows live under the None key: before overwriting our
            # own row, verify it still belongs to the bound tenant.  A row that
            # was re-parented must invalidate the old scope, not be repaired
            # silently by a request holding a stale decision.
            existing = self._repo.get("workspace", None, entity_id)
            if existing is not None:
                self._assert_workspace_row(existing, str(entity_id), scope)
        payload = dict(data)
        # Payload tenant/workspace are forced, never trusted: a caller cannot
        # spoof either value into another tenant's row.
        payload["tenant_id"] = scope.tenant.tenant_id
        payload["workspace_id"] = scope.tenant.workspace_id
        if kind not in self._control and not scope.filter([payload]):
            raise AccessDeniedError(
                "write is outside the authorized row scope")
        return wid, payload

    def put(self, kind: str, workspace_id: str | None, entity_id: str,
            data: dict) -> int:
        scope = self._scope()
        wid, payload = self._prepare_write(kind, workspace_id, entity_id,
                                           data, scope)
        return self._repo.put(kind, wid, entity_id, payload)

    def put_if_version(self, kind: str, workspace_id: str | None,
                       entity_id: str, data: dict,
                       expected_version: int | None) -> int:
        scope = self._scope()
        wid, payload = self._prepare_write(kind, workspace_id, entity_id,
                                           data, scope)
        return self._repo.put_if_version(kind, wid, entity_id, payload,
                                         expected_version)

    def delete(self, kind: str, workspace_id: str | None,
               entity_id: str) -> bool:
        scope = self._scope()
        wid = self._pin(kind, workspace_id, scope, entity_id)
        if scope is not None:
            if kind == "workspace":
                row = self._repo.get("workspace", None, entity_id)
                if row is None:
                    return False
                self._assert_workspace_row(row, str(entity_id), scope)
            elif kind not in self._control:
                # Read the raw row here: a filtered outer read would turn
                # "out of scope" into "not found" and silently skip the
                # explicit deny.
                row = self._repo.get(kind, wid, entity_id)
                if row is None:
                    return False
                if not self._row_tenant_ok(row, scope):
                    self._deny()
                if not scope.filter([self._enrich(row, scope)]):
                    raise AccessDeniedError(
                        "delete is outside the authorized row scope")
        return self._repo.delete(kind, wid, entity_id)

    def _assert_owned_workspace(self, workspace_id: str, scope) -> None:
        """Ownership check for workspace-wide maintenance calls.

        The caller is already pinned to ``scope.tenant.workspace_id``; when the
        workspace row exists it must still belong to the decision's tenant.
        A missing row (fresh, or just deleted by this request) is left to the
        underlying repository, which returns empty maintenance results.
        """
        actual = self._workspace_tenant(workspace_id)
        if actual is not None:
            self._assert_tenant(actual, scope)

    def workspace_kinds(self, workspace_id: str) -> dict[str, int]:
        scope = self._scope()
        if scope is not None:
            if workspace_id != scope.tenant.workspace_id:
                self._deny()
            self._assert_owned_workspace(workspace_id, scope)
        return self._repo.workspace_kinds(workspace_id)

    def delete_workspace_documents(self, workspace_id: str) -> int:
        scope = self._scope()
        if scope is not None:
            if workspace_id != scope.tenant.workspace_id:
                self._deny()
            self._assert_owned_workspace(workspace_id, scope)
        return self._repo.delete_workspace_documents(workspace_id)

    def transaction(self):
        return self._repo.transaction()

    def close(self) -> None:
        closer = getattr(self._repo, "close", None)
        if callable(closer):
            closer()



