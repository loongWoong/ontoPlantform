"""Governance: Lifecycle / Access / Audit (§29)。"""

from __future__ import annotations

import pytest

from onto_platform.domain.governance.model import transition
from onto_platform.errors import AccessDeniedError, LifecycleError, ValidationError


# ----------------------------------------------------------------------
# lifecycle
# ----------------------------------------------------------------------


def test_lifecycle_happy_path():
    assert transition("draft", "validate") == "validated"
    assert transition("validated", "publish") == "published"
    assert transition("published", "deprecate") == "deprecated"


def test_lifecycle_illegal_transitions():
    with pytest.raises(LifecycleError):
        transition("draft", "publish")  # must be validated first
    with pytest.raises(LifecycleError):
        transition("published", "validate")
    with pytest.raises(LifecycleError):
        transition("deprecated", "publish")
    with pytest.raises(LifecycleError):
        transition("draft", "revise")  # revise only from validated
    with pytest.raises(LifecycleError):
        transition("draft", "deprecate")
    assert transition("validated", "revise") == "draft"


# ----------------------------------------------------------------------
# access
# ----------------------------------------------------------------------


def test_viewer_cannot_edit(platform, workspace_id):
    platform.workspaces.add_member(workspace_id, "alice", "viewer", "admin")
    with pytest.raises(AccessDeniedError):
        platform.ontologies.upsert_object(
            workspace_id, "alice", "Road",
            properties=[{"name": "name", "type": "string"}])
    # but can read
    assert platform.ontologies.get(workspace_id, "alice")["name"] == "traffic"


def test_editor_can_edit_but_not_manage_members(platform):
    ws = platform.workspaces.create("新空间", actor="admin")
    wid = ws["id"]
    platform.ontologies.create(wid, "draft-world", actor="admin")
    platform.workspaces.add_member(wid, "bob", "editor", "admin")
    platform.ontologies.upsert_object(
        wid, "bob", "Road",
        properties=[{"name": "name", "type": "string"}])
    with pytest.raises(AccessDeniedError):
        platform.workspaces.add_member(wid, "carol", "viewer", "bob")


def test_outsider_denied(platform, workspace_id):
    with pytest.raises(AccessDeniedError):
        platform.ontologies.get(workspace_id, "mallory")


def test_invoke_requires_membership(platform, workspace_id):
    with pytest.raises(AccessDeniedError):
        platform.capabilities.invoke(workspace_id, "mallory", "Vehicle.recharge",
                                     target="Vehicle/V001", args={"amount": 1})


# ----------------------------------------------------------------------
# audit
# ----------------------------------------------------------------------


def test_audit_records_design_and_runtime_actions(platform, workspace_id):
    entries = platform.ctx.audit.list(workspace_id)
    actions = [e["action"] for e in entries]
    assert "workspace.create" in actions
    assert "ontology.publish" in actions
    assert "data.run_projection" in actions
    assert "capability.publish" in actions

    platform.capabilities.invoke(workspace_id, "admin", "Vehicle.recharge",
                                 target="Vehicle/V001", args={"amount": 1})
    actions = [e["action"] for e in platform.ctx.audit.list(workspace_id)]
    assert "capability.invoke" in actions


def test_published_ontology_is_immutable(platform, workspace_id):
    with pytest.raises(ValidationError):
        platform.ontologies.upsert_object(
            workspace_id, "admin", "Road",
            properties=[{"name": "name", "type": "string"}])
