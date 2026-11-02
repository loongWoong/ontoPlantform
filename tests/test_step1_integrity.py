"""Step-1 backend integrity: Release guard, workspace reset, instance
delete/update doors, and CORS/CSRF hardening.

These are the regression tests for the four defects fixed together:

* a Release that references a since-deleted capability could still be deployed
  (or rolled back to), installing a runtime that contradicts its metadata;
* there was no way to empty a broken workspace short of deleting it;
* instance objects could not be corrected or removed through the API;
* the HTTP adapter answered ``Access-Control-Allow-Origin: *`` to any origin
  and accepted non-JSON state-changing requests.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)
from onto_platform.interfaces.api import SMART_UI_DIR
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta
from tests.conftest import http_call, http_get, http_post, seed_workspace


# ----------------------------------------------------------------------
# 1. Release deployment guard
# ----------------------------------------------------------------------


def test_deploy_refuses_release_with_removed_capability(platform):
    wid = seed_workspace(platform)
    capability = platform.capabilities.list(wid, "admin")[0]
    old_release = platform.releases.list(wid, "admin")["releases"][-1]
    assert capability["id"] in old_release["capability_ids"]

    # deleting the published capability repairs the runtime and cuts a new
    # release; the old one still names the gone capability
    platform.capabilities.delete(wid, capability["id"], "admin")
    listed = platform.releases.list(wid, "admin")
    by_id = {row["id"]: row for row in listed["releases"]}
    assert by_id[old_release["id"]]["missing_capability_ids"] == [capability["id"]]
    assert listed["releases"][-1]["missing_capability_ids"] == []
    # the derived field never rewrites the immutable stored release
    stored = platform.ctx.repo.get("release", wid, old_release["id"])
    assert "missing_capability_ids" not in stored
    assert stored["capability_ids"] == old_release["capability_ids"]

    current = listed["deployment"]
    with pytest.raises(ValidationError) as excinfo:
        platform.releases.deploy(wid, "admin", old_release["id"])
    message = str(excinfo.value)
    assert old_release["id"] in message and capability["id"] in message
    # failed deploy must not move or dirty the deployment pointer
    after = platform.releases.list(wid, "admin")["deployment"]
    assert after["release_id"] == current["release_id"]
    assert after["status"] == current["status"]

    # a release cut after the deletion is still deployable
    new_release = listed["releases"][-1]
    deployed = platform.releases.deploy(wid, "admin", new_release["id"])
    assert deployed["deployment"]["release_id"] == new_release["id"]
    assert deployed["deployment"]["status"] == "active"


def test_rollback_and_canary_go_through_the_same_guard(platform):
    wid = seed_workspace(platform)
    capability = platform.capabilities.list(wid, "admin")[0]
    old_release = platform.releases.list(wid, "admin")["releases"][-1]
    platform.capabilities.delete(wid, capability["id"], "admin")
    current = platform.releases.list(wid, "admin")["deployment"]

    with pytest.raises(ValidationError):
        platform.releases.rollback(wid, "admin")
    assert platform.releases.list(wid, "admin")["deployment"] == current

    with pytest.raises(ValidationError):
        platform.releases.canary(wid, "admin", old_release["id"])
    assert platform.releases.list(wid, "admin")["canary"] is None


# ----------------------------------------------------------------------
# 2. Workspace reset
# ----------------------------------------------------------------------


def test_workspace_reset_clears_content_and_keeps_plumbing(platform):
    wid = seed_workspace(platform)
    platform.llm_config.put(wid, "admin",
                            {"provider": "openai", "api_key": "sk-reset"})
    platform.workspaces.add_member(wid, "bob", "editor", "admin")
    before = platform.ctx.repo.workspace_kinds(wid)
    assert {"ontology", "projection", "capability", "release"} <= set(before)

    result = platform.workspaces.reset(
        wid, "admin", confirm=True, confirm_name="高速运营")

    assert result["workspace_id"] == wid
    assert result["name"] == "高速运营"
    assert result["reset"] is True
    assert result["removed_documents"] > 0
    assert "ontology" in result["removed_kinds"]
    assert "release" in result["removed_kinds"]
    assert "membership" not in result["removed_kinds"]
    assert "llm_config" not in result["removed_kinds"]

    # only the preserved plumbing survives
    assert set(platform.ctx.repo.workspace_kinds(wid)) == {
        "membership", "llm_config"}
    members = {m["user"]: m["role"]
               for m in platform.workspaces.list_members(wid, "admin")}
    assert members == {"admin": "owner", "bob": "editor"}
    assert platform.llm_config.get(wid, "admin")["configured"] is True
    assert platform.workspaces.get(wid, "admin")["name"] == "高速运营"

    # the runtime was dropped and the workspace is now deletable
    assert platform.ctx.gateway_for(wid).published is False
    listed = {w["id"]: w for w in platform.workspaces.list("admin")}
    assert listed[wid]["blockers"] == []
    assert listed[wid]["deletable"] is True

    actions = [a["action"] for a in platform.ctx.audit.list(wid, limit=200)]
    assert "workspace.reset" in actions

    # after a reset the workspace can be deleted like any empty one
    deleted = platform.workspaces.delete(wid, "admin", confirm=True)
    assert deleted["deleted"] is True
    assert platform.ctx.repo.get("workspace", None, wid) is None


def test_workspace_reset_requires_confirmation_and_exact_name(platform):
    wid = seed_workspace(platform)

    with pytest.raises(ValidationError, match="confirmed"):
        platform.workspaces.reset(wid, "admin", confirm=False,
                                  confirm_name="高速运营")
    with pytest.raises(ValidationError, match="confirm_name"):
        platform.workspaces.reset(wid, "admin", confirm=True,
                                  confirm_name="高速")
    # refused resets are not partial
    assert "ontology" in platform.ctx.repo.workspace_kinds(wid)


def test_workspace_reset_requires_owner(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "editor", "admin")

    with pytest.raises(AccessDeniedError):
        platform.workspaces.reset(wid, "bob", confirm=True,
                                  confirm_name="高速运营")
    assert "ontology" in platform.ctx.repo.workspace_kinds(wid)


def test_workspace_reset_over_http(http_server):
    base, wid = http_server
    http_post(base, f"/api/workspaces/{wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})

    status, body = http_post(
        base, f"/api/workspaces/{wid}/reset",
        {"user": "bob", "confirm": True, "confirm_name": "高速运营"})
    assert status == 403

    status, _ = http_post(
        base, f"/api/workspaces/{wid}/reset", {"user": "admin"})
    assert status == 400

    status, _ = http_post(
        base, f"/api/workspaces/{wid}/reset",
        {"user": "admin", "confirm": True, "confirm_name": "wrong"})
    assert status == 400

    status, body = http_post(
        base, f"/api/workspaces/{wid}/reset",
        {"user": "admin", "confirm": True, "confirm_name": "高速运营"})
    assert status == 200
    assert body["reset"] is True and body["removed_documents"] > 0
    status, listed = http_get(base, "/api/workspaces?user=admin")
    by_id = {w["id"]: w for w in listed}
    assert by_id[wid]["deletable"] is True


def test_sqlite_workspace_reset_keeps_plumbing(tmp_path):
    from onto_platform.bootstrap import PlatformConfig, build_platform

    platform = build_platform(PlatformConfig(
        store="sqlite", db_path=str(tmp_path / "platform.db"),
        runtime_data_dir=str(tmp_path / "runtime")))
    try:
        wid = seed_workspace(platform)
        platform.llm_config.put(wid, "admin",
                                {"provider": "openai", "api_key": "sk-sqlite"})
        result = platform.workspaces.reset(
            wid, "admin", confirm=True, confirm_name="高速运营")
        assert result["reset"] is True and result["removed_documents"] > 0
        assert set(platform.ctx.repo.workspace_kinds(wid)) == {
            "membership", "llm_config"}
        members = {m["user"]: m["role"]
                   for m in platform.workspaces.list_members(wid, "admin")}
        assert members == {"admin": "owner"}
        assert platform.llm_config.get(wid, "admin")["configured"] is True
        listed = {w["id"]: w for w in platform.workspaces.list("admin")}
        assert listed[wid]["deletable"] is True
    finally:
        platform.close()


# ----------------------------------------------------------------------
# 3. Instance delete / update
# ----------------------------------------------------------------------


def _state_workspace(platform) -> str:
    wid = platform.workspaces.create("状态", actor="admin")["id"]
    platform.ontologies.create(wid, "demo", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Thing",
        properties=[{"name": "code", "type": "string", "required": True},
                    {"name": "label", "type": "string"},
                    {"name": "status", "type": "string"}],
        identity=["code"])
    platform.ontologies.publish(wid, "admin")
    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(upserts=(
        ObjectUpsert("Thing", "T1",
                     {"code": "C1", "label": "a", "status": "free"}),
        ObjectUpsert("Thing", "T2", {"code": "C2", "label": "b"}),
    )))
    return wid


def test_update_object_validates_and_audits(platform):
    wid = _state_workspace(platform)

    result = platform.state.update_object(
        wid, "admin", "Thing/T1", {"label": "new", "status": "busy"})

    assert result["ref"] == "Thing/T1"
    assert result["changed"] == ["label", "status"]
    assert result["object"]["properties"]["label"] == "new"
    assert result["object"]["properties"]["status"] == "busy"
    actions = [a["action"] for a in platform.ctx.audit.list(wid, limit=200)]
    assert "state.object.update" in actions


def test_update_object_rejects_identity_unknown_and_absent(platform):
    wid = _state_workspace(platform)

    with pytest.raises(ValidationError, match="identity"):
        platform.state.update_object(wid, "admin", "Thing/T1", {"code": "C9"})
    with pytest.raises(ValidationError):
        platform.state.update_object(wid, "admin", "Thing/T1", {"nope": 1})
    with pytest.raises(NotFoundError):
        platform.state.update_object(wid, "admin", "Thing/T404",
                                     {"label": "x"})
    # refused writes changed nothing
    assert platform.ctx.gateway_for(wid).get_object(
        "Thing/T1")["properties"]["code"] == "C1"


def test_delete_objects_reports_missing_and_removes_only_existing(platform):
    wid = _state_workspace(platform)

    result = platform.state.delete_objects(
        wid, "admin", ["Thing/T1", "Thing/GONE"])

    assert result["deleted"] == ["Thing/T1"]
    assert result["missing"] == ["Thing/GONE"]
    assert result["changes"]
    gateway = platform.ctx.gateway_for(wid)
    assert gateway.get_object("Thing/T1") is None
    assert gateway.get_object("Thing/T2") is not None
    actions = [a["action"] for a in platform.ctx.audit.list(wid, limit=200)]
    assert "state.objects.delete" in actions


def test_state_writes_require_editor(platform):
    wid = _state_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.state.update_object(wid, "bob", "Thing/T1", {"label": "x"})
    with pytest.raises(AccessDeniedError):
        platform.state.delete_objects(wid, "bob", ["Thing/T1"])
    assert platform.ctx.gateway_for(wid).get_object("Thing/T1") is not None


def test_state_delete_and_update_over_http(http_server):
    base, wid = http_server
    ref = "Vehicle%2FV001"

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/state/objects?ref={ref}&user=admin",
        {"properties": {"balance": 42}})
    assert status == 200
    assert body["ref"] == "Vehicle/V001"
    assert body["object"]["properties"]["balance"] == 42
    assert body["changed"] == ["balance"]

    status, _headers, body = http_call(
        "DELETE", base, f"/api/workspaces/{wid}/state/objects?ref={ref}&user=admin")
    assert status == 200
    assert body["deleted"] == ["Vehicle/V001"]
    assert body["missing"] == []
    assert body["changes"]

    # a single absent ref is a 404 for both mutating routes
    status, _headers, _body = http_call(
        "DELETE", base, f"/api/workspaces/{wid}/state/objects?ref={ref}&user=admin")
    assert status == 404
    status, _headers, _body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/state/objects?ref={ref}&user=admin",
        {"properties": {"balance": 1}})
    assert status == 404


def test_state_http_requires_editor(http_server):
    base, wid = http_server
    http_post(base, f"/api/workspaces/{wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})

    status, _headers, _body = http_call(
        "PATCH", base,
        f"/api/workspaces/{wid}/state/objects?ref=Vehicle%2FV001&user=bob",
        {"properties": {"balance": 1}})
    assert status == 403
    status, _headers, _body = http_call(
        "DELETE", base,
        f"/api/workspaces/{wid}/state/objects?ref=Vehicle%2FV001&user=bob")
    assert status == 403


# ----------------------------------------------------------------------
# 4. CORS / CSRF
# ----------------------------------------------------------------------


def _acao(headers: dict) -> str | None:
    for key, value in headers.items():
        if key.lower() == "access-control-allow-origin":
            return value
    return None


def test_cross_site_origin_is_rejected_on_every_method(http_server):
    base, wid = http_server
    evil = {"Origin": "http://evil.example"}

    status, headers, _ = http_call("GET", base, "/api/health", headers=evil)
    assert status == 403 and _acao(headers) is None

    status, headers, _ = http_call(
        "POST", base, "/api/workspaces", {"name": "x"}, headers=evil)
    assert status == 403 and _acao(headers) is None

    ref = "Vehicle%2FV001"
    status, headers, _ = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/state/objects?ref={ref}&user=admin",
        {"properties": {"balance": 1}}, headers=evil)
    assert status == 403 and _acao(headers) is None

    status, headers, _ = http_call(
        "DELETE", base, f"/api/workspaces/{wid}/state/objects?ref={ref}&user=admin",
        headers=evil)
    assert status == 403 and _acao(headers) is None

    status, headers, _ = http_call(
        "OPTIONS", base, "/api/workspaces", headers=evil)
    assert status == 403 and _acao(headers) is None


def test_allowed_origin_is_echoed_with_vary_and_patch(platform, http_server):
    base, _wid = http_server
    platform.config.extra["cors_origins"] = ["http://console.example"]

    status, headers, _ = http_call(
        "GET", base, "/api/health", headers={"Origin": "http://console.example"})
    assert status == 200
    assert _acao(headers) == "http://console.example"
    assert headers.get("Vary") == "Origin"
    assert "PATCH" in headers["Access-Control-Allow-Methods"]

    status, headers, _ = http_call(
        "OPTIONS", base, "/api/workspaces",
        headers={"Origin": "http://console.example"})
    assert status == 204
    assert _acao(headers) == "http://console.example"


def test_origin_allowlist_can_come_from_the_environment(http_server, monkeypatch):
    base, _wid = http_server
    monkeypatch.setenv("ONTO_CORS_ORIGINS",
                       "http://env.example, http://other.example")

    status, headers, _ = http_call(
        "GET", base, "/api/health", headers={"Origin": "http://env.example"})
    assert status == 200
    assert _acao(headers) == "http://env.example"


def test_same_origin_request_is_allowed(http_server):
    base, _wid = http_server

    status, headers, _ = http_call(
        "GET", base, "/api/health", headers={"Origin": base})
    assert status == 200
    assert _acao(headers) == base


def test_no_origin_requests_stay_usable(http_server):
    base, _wid = http_server

    status, headers, _ = http_call("GET", base, "/api/health")
    assert status == 200 and _acao(headers) is None

    status, headers, _ = http_call("OPTIONS", base, "/api/workspaces")
    assert status == 204
    methods = headers["Access-Control-Allow-Methods"]
    assert "PATCH" in methods and "DELETE" in methods


def _raw_get(base: str, path: str, headers: dict):
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, dict(res.headers), res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def test_static_assets_are_public_to_an_opaque_iframe_origin(http_server):
    """The sandboxed smart-UI iframe has ``Origin: null``.

    Its module scripts/styles are CORS requests, so static build output must be
    served before the origin gate and with a wildcard grant.  Without this the
    runtime iframe shows an empty page with 403s on ``/smart-ui/assets/*``.
    """
    base, _wid = http_server
    opaque = {"Origin": "null"}

    assert (SMART_UI_DIR / "index.html").is_file(), "smart-ui runtime not built"
    status, headers, html = _raw_get(base, "/smart-ui/", opaque)
    assert status == 200, html
    assert _acao(headers) == "*"
    assert "/smart-ui/assets/" in html

    status, headers, _ = _raw_get(base, "/", opaque)
    assert status == 200
    assert _acao(headers) == "*"


def test_opaque_origin_cannot_call_the_api_directly(http_server):
    """Keep the CSRF boundary: the iframe reaches the API via its parent."""
    base, _wid = http_server
    opaque = {"Origin": "null"}

    status, headers, _ = http_call("GET", base, "/api/health", headers=opaque)
    assert status == 403 and _acao(headers) is None

    status, headers, _ = http_call(
        "OPTIONS", base, "/api/workspaces", headers=opaque)
    assert status == 403 and _acao(headers) is None


def test_post_and_patch_require_json_content_type(http_server):
    base, _wid = http_server
    same_origin = {"Origin": base}

    # a cross-site form can post text/plain without a preflight: refuse it
    status, _, _ = http_call(
        "POST", base, "/api/workspaces", headers={
            **same_origin, "Content-Type": "text/plain"})
    assert status == 400

    status, _, _ = http_call(
        "POST", base, "/api/workspaces", headers=same_origin)
    assert status == 400

    # the normal JSON content type (with charset) is accepted
    status, _, body = http_call(
        "POST", base, "/api/workspaces", {"name": "csrf-safe"},
        headers={**same_origin,
                 "Content-Type": "application/json; charset=utf-8"})
    assert status == 200 and body["name"] == "csrf-safe"

    # ... and an evil origin is still refused before the body is parsed
    status, _, _ = http_call(
        "POST", base, "/api/workspaces", headers={
            "Origin": "http://evil.example",
            "Content-Type": "text/plain"})
    assert status == 403
