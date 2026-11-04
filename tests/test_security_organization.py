"""Organization tree tests (design V0.2: org tree + org_path scope).

Covered here:

* materialized paths are generated from the parent chain, never supplied;
* a corrupt tree (cycle, orphan, forged path, cross-tenant parent) is rejected
  instead of yielding a wider scope;
* membership feeds the deepest ``org_path``/``org_unit`` principal attributes;
* deletion refuses units with members/children, and ``cascade`` removes only an
  empty subtree;
* every write is authorized through ``security.check_action`` and audited.
"""

from __future__ import annotations

import pytest

from onto_platform.application.security import context as auth_context
from onto_platform.application.security.organization import (
    AUDIT_ORG_CREATED,
    AUDIT_ORG_DELETED,
    AUDIT_ORG_MEMBERS_UPDATED,
    ORG_KIND,
    OrganizationService,
)
from onto_platform.domain.security.organization import (
    OrganizationUnit,
    ensure_acyclic,
    org_path_of,
)
from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)
from tests.conftest import make_platform


@pytest.fixture(autouse=True)
def _isolated_security_context():
    """``check_action`` binds a request decision; tests must not inherit it."""
    auth_context.clear()
    yield
    auth_context.clear()


@pytest.fixture()
def org_platform():
    return make_platform()


@pytest.fixture()
def org_wid(org_platform):
    return org_platform.workspaces.create("组织权限", actor="admin")["id"]


@pytest.fixture()
def org(org_platform, org_wid):
    return OrganizationService(
        org_platform.ctx.repo,
        org_platform.ctx.clock,
        org_platform.ctx.id_factory,
        org_platform.ctx.security,
        ctx=org_platform.ctx,
    )


def _units(platform, wid):
    return [OrganizationUnit.from_dict(row)
            for row in platform.ctx.repo.list(ORG_KIND, wid)]


def _audit_actions(platform, wid):
    return [entry.get("action")
            for entry in platform.ctx.audit.list(wid)]


# ---------------------------------------------------------------------------
# paths and tree shape
# ---------------------------------------------------------------------------


def test_path_is_materialized_from_the_parent_chain(org, org_wid):
    root = org.create_unit(org_wid, "admin", "总部")
    region = org.create_unit(org_wid, "admin", "华东大区", root["id"])
    station = org.create_unit(org_wid, "admin", "济南西站", region["id"])

    assert root["path"] == f"/{root['id']}/"
    assert region["path"] == f"/{root['id']}/{region['id']}/"
    assert station["path"] == (
        f"/{root['id']}/{region['id']}/{station['id']}/")
    assert (root["depth"], region["depth"], station["depth"]) == (1, 2, 3)

    listed = org.list_units(org_wid)
    assert [unit["id"] for unit in listed] == [
        root["id"], region["id"], station["id"]]

    tree = org.tree(org_wid)
    assert tree["count"] == 3
    assert [node["id"] for node in tree["roots"]] == [root["id"]]
    assert tree["children"][""] == [root["id"]]
    assert tree["children"][root["id"]] == [region["id"]]
    nested_region = tree["roots"][0]["children"][0]
    assert nested_region["id"] == region["id"]
    assert nested_region["children"][0]["id"] == station["id"]


def test_writes_require_security_manage(org, org_platform, org_wid):
    org_platform.workspaces.add_member(org_wid, "bob", "viewer", actor="admin")
    with pytest.raises(AccessDeniedError):
        org.create_unit(org_wid, "bob", "越权单元")
    with pytest.raises(AccessDeniedError):
        org.create_unit(org_wid, "outsider", "陌生单元")
    # A denied write must not leave a tree row behind.
    assert org.list_units(org_wid) == []


def test_parent_must_exist_and_share_the_tenant(org, org_platform, org_wid):
    with pytest.raises(NotFoundError):
        org.create_unit(org_wid, "admin", "孤儿", "missing-parent")

    # A forged row must be written outside the just-bound decision context
    # (the PEP pins tenant_id to the authorized workspace).
    auth_context.clear()
    org_platform.ctx.repo.put(ORG_KIND, org_wid, "foreign", {
        "id": "foreign",
        "name": "别的租户",
        "parent_id": "",
        "path": "/foreign/",
        "tenant_id": "other-tenant",
        "workspace_id": org_wid,
        "members": [],
        "created_at": "",
    })
    with pytest.raises(ValidationError, match="different tenant"):
        org.create_unit(org_wid, "admin", "跨租户孩子", "foreign")


def test_self_parent_forged_path_and_cycles_are_rejected(org_platform, org_wid):
    with pytest.raises(ValidationError, match="own parent"):
        OrganizationUnit(id="a", name="A", parent_id="a", path="/a/a/")
    with pytest.raises(ValidationError, match="parent chain"):
        OrganizationUnit(id="a", name="A", path="/b/a/")

    # Cyclic rows can only come from a buggy/older writer; every read must
    # refuse them instead of returning an ``org_path`` that could widen scope.
    auth_context.clear()
    org_platform.ctx.repo.put(ORG_KIND, org_wid, "a", {
        "id": "a", "name": "A", "parent_id": "b", "path": "/b/a/"})
    org_platform.ctx.repo.put(ORG_KIND, org_wid, "b", {
        "id": "b", "name": "B", "parent_id": "a", "path": "/a/b/"})
    with pytest.raises(ValidationError, match="cycle"):
        ensure_acyclic(_units(org_platform, org_wid))

    service = OrganizationService(
        org_platform.ctx.repo, org_platform.ctx.clock,
        org_platform.ctx.id_factory, org_platform.ctx.security,
        ctx=org_platform.ctx)
    with pytest.raises(ValidationError):
        service.tree(org_wid)

    # An orphaned parent reference is equally unusable.
    orphan_platform = make_platform()
    orphan_wid = orphan_platform.workspaces.create("孤儿树", actor="admin")["id"]
    orphan_platform.ctx.repo.put(ORG_KIND, orphan_wid, "x", {
        "id": "x", "name": "X", "parent_id": "ghost", "path": "/ghost/x/"})
    orphan_service = OrganizationService(
        orphan_platform.ctx.repo, orphan_platform.ctx.clock,
        orphan_platform.ctx.id_factory, orphan_platform.ctx.security,
        ctx=orphan_platform.ctx)
    with pytest.raises(ValidationError, match="missing parent"):
        orphan_service.list_units(orphan_wid)


# ---------------------------------------------------------------------------
# membership -> principal attributes
# ---------------------------------------------------------------------------


def test_membership_yields_the_deepest_org_path(org, org_platform, org_wid):
    root = org.create_unit(org_wid, "admin", "总部")
    region = org.create_unit(org_wid, "admin", "华东大区", root["id"])
    station = org.create_unit(org_wid, "admin", "济南西站", region["id"])
    org.set_members(org_wid, "admin", root["id"], ["bob"])
    org.set_members(org_wid, "admin", region["id"], ["alice"])
    org.set_members(org_wid, "admin", station["id"], ["alice"])

    attributes = org.attributes_for("alice", org_wid)
    assert attributes == {"org_path": station["path"],
                          "org_unit": station["id"]}
    assert org.attributes_for("bob", org_wid) == {
        "org_path": root["path"], "org_unit": root["id"]}
    assert org.attributes_for("carol", org_wid) == {}

    units = _units(org_platform, org_wid)
    assert org_path_of(units, "alice") == station["path"]
    assert org_path_of(units, "carol") == ""

    principal = org.principal_with_org("alice", org_wid)
    assert principal.attributes["org_path"] == station["path"]
    assert principal.attributes["org_unit"] == station["id"]
    assert principal.id == "alice"


def test_set_members_validates_and_deduplicates(org, org_wid):
    root = org.create_unit(org_wid, "admin", "总部")
    with pytest.raises(ValidationError, match="list of user ids"):
        org.set_members(org_wid, "admin", root["id"], "alice")
    with pytest.raises(ValidationError):
        org.set_members(org_wid, "admin", root["id"], ["alice", " "])
    with pytest.raises(NotFoundError):
        org.set_members(org_wid, "admin", "missing", ["alice"])

    updated = org.set_members(org_wid, "admin", root["id"],
                              ["alice", "alice", "bob"])
    assert updated["members"] == ["alice", "bob"]


# ---------------------------------------------------------------------------
# deletion
# ---------------------------------------------------------------------------


def test_delete_refuses_units_with_members_or_children(org, org_wid):
    root = org.create_unit(org_wid, "admin", "总部")
    region = org.create_unit(org_wid, "admin", "华东大区", root["id"])

    org.set_members(org_wid, "admin", region["id"], ["alice"])
    with pytest.raises(ValidationError, match="members"):
        org.delete_unit(org_wid, "admin", region["id"])

    with pytest.raises(ValidationError, match="child units"):
        org.delete_unit(org_wid, "admin", root["id"])
    with pytest.raises(ValidationError, match="members"):
        org.delete_unit(org_wid, "admin", root["id"], cascade=True)
    with pytest.raises(NotFoundError):
        org.delete_unit(org_wid, "admin", "missing")

    assert len(org.list_units(org_wid)) == 2


def test_cascade_deletes_only_an_empty_subtree(org, org_wid):
    root = org.create_unit(org_wid, "admin", "总部")
    region = org.create_unit(org_wid, "admin", "华东大区", root["id"])
    station = org.create_unit(org_wid, "admin", "济南西站", region["id"])
    org.set_members(org_wid, "admin", root["id"], ["alice"])

    with pytest.raises(ValidationError, match="members"):
        org.delete_unit(org_wid, "admin", root["id"], cascade=True)

    org.set_members(org_wid, "admin", root["id"], [])
    deleted = org.delete_unit(org_wid, "admin", root["id"], cascade=True)
    assert deleted["deleted"] is True
    assert deleted["id"] == root["id"]
    assert set(deleted["cascaded"]) == {region["id"], station["id"]}
    assert org.list_units(org_wid) == []


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_writes_emit_audit_events(org, org_platform, org_wid):
    root = org.create_unit(org_wid, "admin", "总部")
    org.set_members(org_wid, "admin", root["id"], ["alice"])
    org.set_members(org_wid, "admin", root["id"], [])
    org.delete_unit(org_wid, "admin", root["id"])

    actions = _audit_actions(org_platform, org_wid)
    assert AUDIT_ORG_CREATED in actions
    assert AUDIT_ORG_MEMBERS_UPDATED in actions
    assert AUDIT_ORG_DELETED in actions

    entries = [entry for entry in org_platform.ctx.audit.list(org_wid)
               if entry.get("target") == root["id"]]
    by_action = {entry["action"]: entry for entry in entries}
    assert by_action[AUDIT_ORG_CREATED]["detail"]["path"] == root["path"]
    assert by_action[AUDIT_ORG_MEMBERS_UPDATED]["detail"]["members"] == []
    assert by_action[AUDIT_ORG_CREATED]["actor"] == "admin"
