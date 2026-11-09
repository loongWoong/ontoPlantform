"""P2.2-A: repository-level tenant hard boundary (L1).

Before this hardening the governed repository only pinned the *workspace*: a
forged authorization context (tenant B bound to workspace A) still passed the
workspace equality check.  These tests prove the storage boundary now also
enforces tenant consistency, so cross-tenant rows cannot be read, written or
deleted even with a valid-looking decision, and a re-parented workspace row
invalidates its old scope.

They exercise the installed ``platform.ctx.repo`` (the same object use cases
use), not a hand-built wrapper, except for the injectable-resolver unit test.
"""

from __future__ import annotations

import pytest

from onto_platform.application.security import context as auth_context
from onto_platform.application.security.service import AuthorizedScope
from onto_platform.domain.security.model import (
    Decision,
    Principal,
    TenantContext,
)
from onto_platform.errors import AccessDeniedError
from onto_platform.infrastructure.persistence.scoped_repo import (
    GovernedRepository,
)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


@pytest.fixture()
def tenants(platform):
    """Two independent workspaces (= tenants in V0.1)."""
    first = platform.workspaces.create("租户A", actor="admin")["id"]
    second = platform.workspaces.create("租户B", actor="admin")["id"]
    return first, second


def _system_repo(platform):
    """The unguarded repository used by migrations/tests to seed rows."""
    return platform.ctx.repo.raw


def _bind_scope(workspace_id: str, tenant_id: str | None = None):
    """Bind an ALLOW decision for ``workspace_id`` (forged when tenant differs)."""
    tenant_id = tenant_id or workspace_id
    principal = Principal(id="alice", kind="user", tenant_id=tenant_id,
                          workspace_id=workspace_id, roles=("owner",))
    tenant = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id)
    decision = Decision(
        allow=True, reason="p2_tenant_hardening", action="object.read",
        principal_id="alice", tenant_id=tenant_id, workspace_id=workspace_id,
        decision_id="dec_p2_test", resource_type="object",
        resource_id=workspace_id)
    return auth_context.bind(decision, principal, tenant)


def _workspace_row(platform, workspace_id: str) -> dict:
    return _system_repo(platform).get("workspace", None, workspace_id)


# ----------------------------------------------------------------------
# same-tenant access still works
# ----------------------------------------------------------------------


def test_valid_scope_reads_writes_and_deletes_its_own_workspace(platform,
                                                                tenants):
    first, second = tenants
    raw = _system_repo(platform)
    raw.put("vehicle", first, "V-A", {"plate": "鲁A"})
    raw.put("vehicle", second, "V-B", {"plate": "鲁B"})

    token = _bind_scope(first)
    try:
        repo = platform.ctx.repo
        assert repo.get("vehicle", first, "V-A")["plate"] == "鲁A"
        # None is pinned to the current workspace, never "all workspaces".
        assert repo.get("vehicle", None, "V-A")["plate"] == "鲁A"
        assert [row["plate"] for row in repo.list("vehicle", first)] == ["鲁A"]

        repo.put("vehicle", first, "V-N", {"plate": "新增"})
        stored = repo.get("vehicle", first, "V-N")
        assert stored["plate"] == "新增"
        assert stored["workspace_id"] == first
        assert stored["tenant_id"] == first

        assert repo.delete("vehicle", first, "V-N") is True
        assert repo.get("vehicle", first, "V-N") is None

        # own workspace document + the workspace list
        workspace = repo.get("workspace", None, first)
        assert workspace["id"] == first
        assert workspace["tenant_id"] == first
        assert [row["id"] for row in repo.list("workspace")] == [first]
    finally:
        auth_context.reset(token)


# ----------------------------------------------------------------------
# forged context: tenant B mounted on workspace A
# ----------------------------------------------------------------------


def test_forged_cross_tenant_scope_is_denied_everywhere(platform, tenants):
    first, second = tenants
    raw = _system_repo(platform)
    raw.put("vehicle", first, "V-A", {"plate": "鲁A"})

    token = _bind_scope(first, tenant_id=second)  # forged: wrong tenant
    try:
        repo = platform.ctx.repo
        with pytest.raises(AccessDeniedError):
            repo.get("vehicle", first, "V-A")
        with pytest.raises(AccessDeniedError):
            repo.get_versioned("vehicle", first, "V-A")
        with pytest.raises(AccessDeniedError):
            repo.list("vehicle", first)
        with pytest.raises(AccessDeniedError):
            repo.put("vehicle", first, "V-X", {"plate": "X"})
        with pytest.raises(AccessDeniedError):
            repo.delete("vehicle", first, "V-A")
        # workspace documents at the None key are tenant-checked as well
        with pytest.raises(AccessDeniedError):
            repo.get("workspace", None, first)
        with pytest.raises(AccessDeniedError):
            repo.list("workspace")
        with pytest.raises(AccessDeniedError):
            repo.put("workspace", None, first,
                     {**_workspace_row(platform, first), "name": "hijacked"})
        with pytest.raises(AccessDeniedError):
            repo.delete("workspace", None, first)
    finally:
        auth_context.reset(token)

    # nothing was written or deleted through the forged decision
    assert raw.get("vehicle", first, "V-A")["plate"] == "鲁A"
    assert raw.get("vehicle", first, "V-X") is None
    assert _workspace_row(platform, first)["name"] == "租户A"


def test_workspace_documents_cannot_be_reached_by_another_tenant(platform,
                                                                 tenants):
    first, second = tenants
    token = _bind_scope(first)
    try:
        repo = platform.ctx.repo
        # another workspace's document lives at the same None key but its id
        # is not ours: neither read nor write may touch it.
        with pytest.raises(AccessDeniedError):
            repo.get("workspace", None, second)
        with pytest.raises(AccessDeniedError):
            repo.put("workspace", None, second,
                     {**_workspace_row(platform, second), "name": "hijacked"})
        with pytest.raises(AccessDeniedError):
            repo.delete("workspace", None, second)
        with pytest.raises(AccessDeniedError):
            repo.list("workspace", second)
    finally:
        auth_context.reset(token)
    assert _workspace_row(platform, second)["name"] == "租户B"


# ----------------------------------------------------------------------
# payload spoofing is pinned, not trusted
# ----------------------------------------------------------------------


def test_write_payload_cannot_spoof_tenant_or_workspace(platform, tenants):
    first, second = tenants
    token = _bind_scope(first)
    try:
        repo = platform.ctx.repo
        repo.put("vehicle", first, "V-S", {
            "plate": "伪造", "tenant_id": second, "workspace_id": second})
        stored = repo.get("vehicle", first, "V-S")
        assert stored["tenant_id"] == first
        assert stored["workspace_id"] == first
    finally:
        auth_context.reset(token)

    # the row really is in A: B's scope cannot see it
    token_b = _bind_scope(second)
    try:
        assert platform.ctx.repo.get("vehicle", second, "V-S") is None
        with pytest.raises(AccessDeniedError):
            platform.ctx.repo.get("vehicle", first, "V-S")
    finally:
        auth_context.reset(token_b)


def test_foreign_tenant_rows_stored_under_our_key_are_not_visible(platform,
                                                                  tenants):
    first, second = tenants
    # simulate a row written by a buggy/legacy path: our workspace key, but an
    # explicit foreign tenant marker
    _system_repo(platform).put("vehicle", first, "V-F", {
        "plate": "外来", "tenant_id": second, "workspace_id": first})

    token = _bind_scope(first)
    try:
        repo = platform.ctx.repo
        with pytest.raises(AccessDeniedError):
            repo.get("vehicle", first, "V-F")
        assert repo.list("vehicle", first) == []
        with pytest.raises(AccessDeniedError):
            repo.delete("vehicle", first, "V-F")
    finally:
        auth_context.reset(token)


# ----------------------------------------------------------------------
# workspace row re-parenting invalidates the old scope
# ----------------------------------------------------------------------


def test_reparented_workspace_row_invalidates_the_old_scope(platform, tenants):
    first, second = tenants
    raw = _system_repo(platform)
    raw.put("vehicle", first, "V-A", {"plate": "鲁A"})
    token = _bind_scope(first)
    try:
        # an operator re-parents the workspace to another tenant
        row = _workspace_row(platform, first)
        raw.put("workspace", None, first, {**row, "tenant_id": second, "id": first})

        repo = platform.ctx.repo
        with pytest.raises(AccessDeniedError):
            repo.get("vehicle", first, "V-A")
        with pytest.raises(AccessDeniedError):
            repo.list("vehicle", first)
        with pytest.raises(AccessDeniedError):
            repo.put("vehicle", first, "V-X", {"plate": "X"})
        with pytest.raises(AccessDeniedError):
            repo.delete("vehicle", first, "V-A")
        with pytest.raises(AccessDeniedError):
            repo.get("workspace", None, first)
        with pytest.raises(AccessDeniedError):
            repo.list("workspace")
        # a stale decision cannot "repair" the tenant by overwriting the row
        with pytest.raises(AccessDeniedError):
            repo.put("workspace", None, first, {**row, "tenant_id": first})
    finally:
        auth_context.reset(token)


# ----------------------------------------------------------------------
# control-plane documents
# ----------------------------------------------------------------------


def test_control_documents_cannot_cross_workspaces(platform, tenants):
    first, second = tenants
    raw = _system_repo(platform)
    raw.put("tenant", None, "tenant-b", {"id": "tenant-b", "name": "B租户"})

    token = _bind_scope(first)
    try:
        repo = platform.ctx.repo
        assert repo.get("membership", first, "admin")["role"] == "owner"

        for call in (
            lambda: repo.get("membership", second, "admin"),
            lambda: repo.list("membership", second),
            lambda: repo.put("membership", second, "eve",
                             {"user": "eve", "role": "owner"}),
            lambda: repo.delete("membership", second, "admin"),
            lambda: repo.get("tenant", second, "tenant-b"),
        ):
            with pytest.raises(AccessDeniedError):
                call()

        # writing a foreign tenant id through the None key stays under our
        # own storage key and never overwrites the other tenant's row
        repo.put("tenant", None, "tenant-b", {"id": "tenant-b", "name": "hacked"})
        assert raw.get("tenant", None, "tenant-b")["name"] == "B租户"
    finally:
        auth_context.reset(token)


# ----------------------------------------------------------------------
# system code (no authorization context) keeps working
# ----------------------------------------------------------------------


def test_system_code_without_context_is_unchanged(platform, tenants):
    first, second = tenants
    auth_context.clear()
    raw = _system_repo(platform)
    raw.put("vehicle", first, "V-A", {"plate": "鲁A"})

    repo = platform.ctx.repo
    # no active decision: the original pass-through behaviour
    assert repo.get("vehicle", second, "V-B") is None
    repo.put("vehicle", second, "V-B", {"plate": "鲁B"})
    assert repo.get("vehicle", second, "V-B")["plate"] == "鲁B"
    assert {row["id"] for row in repo.list("workspace")} >= {first, second}
    assert repo.delete("vehicle", second, "V-B") is True


# ----------------------------------------------------------------------
# injectable tenant resolver (no application/security import at runtime)
# ----------------------------------------------------------------------


def test_tenant_resolver_is_injectable_and_enforced(platform):
    raw = _system_repo(platform)
    raw.put("vehicle", "w-1", "V-1", {"plate": "1"})
    tenant = TenantContext(tenant_id="t-1", workspace_id="w-1")
    principal = Principal(id="alice", tenant_id="t-1", workspace_id="w-1")
    decision = Decision(allow=True, reason="injected", action="object.read",
                        principal_id="alice", tenant_id="t-1",
                        workspace_id="w-1")
    scope = AuthorizedScope(decision=decision, principal=principal,
                            tenant=tenant)
    repo = GovernedRepository(
        raw, scope_provider=lambda: scope,
        tenant_resolver=lambda workspace_id: "t-other")

    with pytest.raises(AccessDeniedError):
        repo.get("vehicle", "w-1", "V-1")
    with pytest.raises(AccessDeniedError):
        repo.list("vehicle", "w-1")
