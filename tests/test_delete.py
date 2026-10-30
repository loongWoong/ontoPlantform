"""Delete flows across ontology, data, capability and application domains.

Deletion is not a blind repository ``delete``: references have to be repaired
or refused explicitly, and deleting a published capability must update the
runtime contract and cut a Release.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

import pytest

from onto_platform.errors import ValidationError
from tests.conftest import http_delete, http_get, http_post


def _draft_workspace(platform) -> str:
    wid = platform.workspaces.create("删除测试", actor="admin")["id"]
    platform.ontologies.create(wid, "demo", actor="admin")
    for name in ("Car", "Driver"):
        platform.ontologies.upsert_object(
            wid, "admin", name,
            properties=[{"name": "plate", "type": "string"}])
    platform.ontologies.upsert_relation(wid, "admin", "drives", "Driver", "Car")
    platform.ontologies.add_term(wid, "admin", "车辆", "Car")
    platform.ontologies.add_term(wid, "admin", "行驶", "drives", kind="relation")
    platform.ontologies.add_term(wid, "admin", "车牌", "Car.plate", kind="property")
    return wid


def test_ontology_draft_removes_rows_but_keeps_references_safe(platform):
    wid = _draft_workspace(platform)

    # a relation (domain guard) and vocabulary terms still reference Car
    with pytest.raises(ValidationError):
        platform.ontologies.remove_object(wid, "admin", "Car")
    assert "Car" in platform.ontologies.get(wid, actor="admin")["objects"]

    with pytest.raises(ValidationError, match="vocabulary"):
        platform.ontologies.remove_relation(wid, "admin", "drives")

    # remove the dependent rows first, then the object types
    platform.ontologies.remove_term(wid, "admin", "车牌")
    platform.ontologies.remove_term(wid, "admin", "行驶")
    ontology = platform.ontologies.remove_relation(wid, "admin", "drives")
    assert "drives" not in ontology["relations"]
    platform.ontologies.remove_term(wid, "admin", "车辆")
    ontology = platform.ontologies.remove_object(wid, "admin", "Car")
    assert "Car" not in ontology["objects"]
    assert "Driver" in ontology["objects"]

    actions = [a["action"] for a in platform.ctx.audit.list(wid)]
    assert "ontology.remove_object" in actions
    assert "ontology.remove_relation" in actions
    assert "ontology.remove_term" in actions


def test_published_ontology_rows_cannot_be_deleted(platform, workspace_id):
    with pytest.raises(ValidationError):
        platform.ontologies.remove_object(workspace_id, "admin", "Vehicle")
    with pytest.raises(ValidationError):
        platform.ontologies.remove_relation(workspace_id, "admin", "passes")
    with pytest.raises(ValidationError):
        platform.ontologies.remove_term(workspace_id, "admin", "车辆")


def test_ontology_object_delete_blocks_capability_and_projection_references(platform):
    wid = _draft_workspace(platform)
    platform.capabilities.create(
        wid, "admin", key="wash", name="洗车", target_type="Car",
        implementation={"kind": "python", "source": "def run(target, args):\n    return {}\n"})
    with pytest.raises(ValidationError, match="capabilities"):
        platform.ontologies.remove_object(wid, "admin", "Car")

    platform.capabilities.delete(wid, platform.capabilities.list(wid, "admin")[0]["id"], "admin")
    source = platform.data.connect_source(
        wid, "admin", "车辆库", "static",
        assets=[{"name": "cars", "config": {"rows": [{"plate": "A1"}]}}])
    platform.data.create_projection(
        wid, "admin", "车辆映射", source["id"],
        object_mappings=[{"asset": "cars", "object_type": "Car", "id_field": "plate",
                          "fields": [{"source_field": "plate", "property": "plate"}]}])
    with pytest.raises(ValidationError, match="projections"):
        platform.ontologies.remove_object(wid, "admin", "Car")


def test_datasource_delete_is_blocked_until_projections_are_gone(platform, workspace_id):
    sources = platform.data.list_sources(workspace_id, "admin")
    projections = platform.data.list_projections(workspace_id, "admin")
    with pytest.raises(ValidationError, match="projections"):
        platform.data.delete_source(workspace_id, "admin", sources[0]["id"])

    result = platform.data.delete_projection(
        workspace_id, "admin", projections[0]["id"])
    assert result["deleted"] is True
    assert result["removed_runs"] >= 1
    assert platform.data.list_projections(workspace_id, "admin") == []
    assert platform.ctx.repo.list("projection_run", workspace_id) == []

    # runtime objects materialized by the deleted mapping are not business data
    runtime = platform.ontologies.describe_runtime(workspace_id, "admin")
    assert {item["name"] for item in runtime["objects"]} == {"Vehicle", "TollStation"}

    result = platform.data.delete_source(workspace_id, "admin", sources[0]["id"])
    assert result["deleted"] is True
    assert platform.data.list_sources(workspace_id, "admin") == []


def test_deleting_published_capability_updates_runtime_apps_and_release(
        platform, workspace_id):
    capability = platform.capabilities.list(workspace_id, "admin")[0]
    app = platform.applications.create(workspace_id, "admin", "运营台")
    platform.applications.attach_capability(
        workspace_id, app["id"], "admin", capability["id"])

    result = platform.capabilities.delete(
        workspace_id, capability["id"], "admin")

    assert result["deleted"] is True
    assert result["detached_from"] == ["运营台"]
    assert {fn["name"] for fn in result["runtime"]["functions"]} == set()
    assert platform.capabilities.list(workspace_id, "admin") == []
    assert platform.applications.get(
        workspace_id, app["id"], "admin")["capabilities"] == []

    releases = platform.releases.list(workspace_id, "admin")
    release_rows = releases["releases"] if isinstance(releases, dict) else releases
    assert release_rows[-1]["id"] == result["release"]["id"]
    assert release_rows[-1]["capability_ids"] == []


def test_published_capability_delete_while_newer_draft_is_active(
        platform, workspace_id):
    capability = platform.capabilities.list(workspace_id, "admin")[0]
    app = platform.applications.create(workspace_id, "admin", "运营台")
    platform.applications.attach_capability(
        workspace_id, app["id"], "admin", capability["id"])
    platform.ontologies.revise(workspace_id, "admin")

    result = platform.capabilities.delete(
        workspace_id, capability["id"], "admin")

    assert result["deleted"] is True
    assert result["detached_from"] == ["运营台"]
    assert {fn["name"] for fn in result["runtime"]["functions"]} == set()
    assert platform.capabilities.list(workspace_id, "admin") == []
    # the newer draft is untouched and still the active ontology row
    active = platform.ontologies.get(workspace_id, "admin")
    assert active["lifecycle"] == "draft"
    assert active["version"] == 2
    # the release describes the published revision that was actually installed
    assert result["release"]["schema_version"] == 1


def test_application_nested_removal_and_delete(platform, workspace_id):
    capability = platform.capabilities.list(workspace_id, "admin")[0]
    app = platform.applications.create(workspace_id, "admin", "运营台")
    platform.applications.add_view(
        workspace_id, app["id"], "admin", "list", object_type="Vehicle",
        query_alias="vehicles")
    platform.applications.add_query(
        workspace_id, app["id"], "admin", "vehicles", object_type="Vehicle")
    platform.applications.add_agent(
        workspace_id, app["id"], "admin", "assistant", goal="总结车辆")
    platform.applications.attach_capability(
        workspace_id, app["id"], "admin", capability["id"])

    with pytest.raises(ValidationError, match="views"):
        platform.applications.remove_query(
            workspace_id, app["id"], "vehicles", "admin")

    platform.applications.remove_view(workspace_id, app["id"], "list", "admin")
    platform.applications.remove_query(workspace_id, app["id"], "vehicles", "admin")
    platform.applications.remove_agent(workspace_id, app["id"], "assistant", "admin")
    platform.applications.detach_capability(
        workspace_id, app["id"], capability["id"], "admin")

    result = platform.applications.delete(workspace_id, app["id"], "admin")
    assert result["deleted"] is True
    assert platform.applications.list(workspace_id, "admin") == []
    actions = [a["action"] for a in platform.ctx.audit.list(workspace_id)]
    assert "application.remove_view" in actions
    assert "application.remove_query" in actions
    assert "application.remove_agent" in actions
    assert "application.detach_capability" in actions
    assert "application.delete" in actions


def test_delete_routes_and_cors_over_http(http_server):
    base, wid = http_server

    status, body = http_post(base, f"/api/workspaces/{wid}/applications",
                             {"name": "待删除应用"})
    assert status == 200
    app_id = body["id"]
    status, body = http_delete(
        base, f"/api/workspaces/{wid}/applications/{app_id}?user=admin")
    assert status == 200
    assert body["deleted"] is True
    status, body = http_get(base, f"/api/workspaces/{wid}/applications/{app_id}?user=admin")
    assert status == 404

    # all four domains expose their delete route
    sources = http_get(base, f"/api/workspaces/{wid}/datasources?user=admin")[1]
    projections = http_get(base, f"/api/workspaces/{wid}/projections?user=admin")[1]
    assert http_delete(
        base, f"/api/workspaces/{wid}/projections/{projections[0]['id']}?user=admin")[0] == 200
    assert http_delete(
        base, f"/api/workspaces/{wid}/datasources/{sources[0]['id']}?user=admin")[0] == 200
    capability = http_get(base, f"/api/workspaces/{wid}/capabilities?user=admin")[1][0]
    assert http_delete(
        base, f"/api/workspaces/{wid}/capabilities/{capability['id']}?user=admin")[0] == 200

    # the HTTP adapter advertises DELETE in its preflight response
    request = urllib.request.Request(
        base + f"/api/workspaces/{wid}/applications", method="OPTIONS")
    with urllib.request.urlopen(request) as response:
        assert "DELETE" in response.headers["Access-Control-Allow-Methods"]

    # a published ontology row is still refused with a structured error
    status, body = http_delete(
        base, f"/api/workspaces/{wid}/ontology/vocabulary/"
              f"{urllib.parse.quote('车辆')}?user=admin")
    assert status >= 400
    assert body["code"] == "validation"
