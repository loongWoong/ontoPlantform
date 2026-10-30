"""Deleting an empty workspace, and refusing to delete one with content."""

from __future__ import annotations

import pytest

from onto_platform.errors import AccessDeniedError, ValidationError
from tests.conftest import http_get, http_post, seed_workspace


def test_empty_workspace_is_deleted_with_memberships(platform):
    ws = platform.workspaces.create("临时空空间", actor="admin")

    listed = {w["id"]: w for w in platform.workspaces.list("admin")}
    assert listed[ws["id"]]["deletable"] is True
    assert listed[ws["id"]]["blockers"] == []

    result = platform.workspaces.delete(ws["id"], "admin", confirm=True)
    assert result["deleted"] is True
    assert result["removed_documents"] >= 2  # workspace + membership
    assert ws["id"] not in {w["id"] for w in platform.workspaces.list("admin")}
    assert platform.ctx.repo.get("workspace", None, ws["id"]) is None

    # The append-only audit evidence survives the delete.
    audit = platform.ctx.audit.list(ws["id"])
    assert any(e["action"] == "workspace.delete" for e in audit)


def test_incidental_runtime_noise_does_not_block_deletion(platform):
    ws = platform.workspaces.create("只有事件", actor="admin")
    platform.ctx.repo.put("event", ws["id"], "evt-1",
                          {"id": "evt-1", "type": "workspace.note"})
    platform.ctx.repo.put("controller_state", ws["id"], "controller",
                          {"workspace_id": ws["id"], "last_tick": "t0"})

    result = platform.workspaces.delete(ws["id"], "admin", confirm=True)
    assert result["deleted"] is True
    assert platform.ctx.repo.workspace_kinds(ws["id"]) == {}


def test_workspace_with_ontology_cannot_be_deleted(platform):
    wid = seed_workspace(platform)

    with pytest.raises(ValidationError, match="not empty"):
        platform.workspaces.delete(wid, "admin", confirm=True)

    # Refusal is not partial: the workspace and its documents are untouched.
    assert platform.workspaces.get(wid, "admin")["id"] == wid
    assert platform.ontologies.get(wid, "admin")["name"] == "traffic"


def test_delete_requires_explicit_confirmation(platform):
    ws = platform.workspaces.create("需要确认", actor="admin")

    with pytest.raises(ValidationError, match="confirmed"):
        platform.workspaces.delete(ws["id"], "admin", confirm=False)

    assert ws["id"] in {w["id"] for w in platform.workspaces.list("admin")}


def test_only_owner_may_delete(platform):
    ws = platform.workspaces.create("权限", actor="admin")
    platform.workspaces.add_member(ws["id"], "viewer-user", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.workspaces.delete(ws["id"], "viewer-user", confirm=True)

    assert ws["id"] in {w["id"] for w in platform.workspaces.list("admin")}


def test_sqlite_workspace_delete_removes_documents(sqlite_platform):
    platform = sqlite_platform
    ws = platform.workspaces.create("sqlite-empty", actor="admin")
    platform.workspaces.add_member(ws["id"], "bob", "editor", "admin")

    platform.workspaces.delete(ws["id"], "admin", confirm=True)

    assert platform.ctx.repo.workspace_kinds(ws["id"]) == {}
    assert ws["id"] not in {w["id"] for w in platform.workspaces.list("admin")}
    assert any(e["action"] == "workspace.delete"
               for e in platform.ctx.audit.list(ws["id"]))


def test_workspace_delete_over_http(http_server):
    base, seeded_wid = http_server

    status, ws = http_post(base, "/api/workspaces",
                           {"name": "HTTP 临时", "user": "admin"})
    assert status == 200

    status, listed = http_get(base, "/api/workspaces?user=admin")
    assert status == 200
    by_id = {w["id"]: w for w in listed}
    assert by_id[ws["id"]]["deletable"] is True
    assert "ontology" in by_id[seeded_wid]["blockers"]

    status, _ = http_post(base, f"/api/workspaces/{ws['id']}/delete",
                          {"user": "admin"})
    assert status == 400  # confirm is mandatory

    status, result = http_post(base, f"/api/workspaces/{ws['id']}/delete",
                               {"user": "admin", "confirm": True})
    assert status == 200 and result["deleted"] is True

    status, listed = http_get(base, "/api/workspaces?user=admin")
    assert ws["id"] not in {w["id"] for w in listed}

    status, payload = http_post(
        base, f"/api/workspaces/{seeded_wid}/delete",
        {"user": "admin", "confirm": True})
    assert status == 400 and "not empty" in payload["error"]
    status, _ = http_get(base, f"/api/workspaces/{seeded_wid}?user=admin")
    assert status == 200
