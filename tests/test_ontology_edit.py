"""Second-step A: ontology metadata editing, draft discard, property guard.

Covers the design-time editing loop that was missing for the console:

* ``PATCH /api/workspaces/{wid}/ontology`` edits name/description/profile of a
  draft only (a published artifact is immutable, revisions are the way out);
* ``POST .../ontology/discard`` drops a revision draft and restores the exact
  published version it replaced, without touching the runtime or the
  deployment pointer;
* dropping a property from an object type is refused while the aggregate
  (identity / state machine / vocabulary) or a sibling asset (projection /
  application view or query) still references it.
"""

from __future__ import annotations

import pytest

from onto_platform.domain.governance.model import transition
from onto_platform.errors import AccessDeniedError, ValidationError
from tests.conftest import http_call, http_get, http_post, seed_workspace


def _empty_ontology(platform, name: str = "编辑测试") -> str:
    wid = platform.workspaces.create("本体编辑", actor="admin")["id"]
    platform.ontologies.create(wid, name, actor="admin", description="初始说明")
    return wid


def _actions(platform, wid: str) -> list[str]:
    return [entry["action"] for entry in platform.ctx.audit.list(wid, limit=500)]


# ----------------------------------------------------------------------
# 1. ontology metadata editing
# ----------------------------------------------------------------------


def test_update_meta_edits_draft_fields(platform):
    wid = _empty_ontology(platform)

    result = platform.ontologies.update_meta(
        wid, "admin", name="  车辆本体  ", description="新的说明", profile="open")

    assert result["name"] == "车辆本体"
    assert result["description"] == "新的说明"
    assert result["profile"] == "open"
    assert result["lifecycle"] == "draft"

    current = platform.ontologies.get(wid, "admin")
    assert current["name"] == "车辆本体"
    assert current["description"] == "新的说明"
    assert current["profile"] == "open"
    assert "ontology.update_meta" in _actions(platform, wid)


def test_update_meta_rejects_published_and_bad_values(platform):
    published_wid = seed_workspace(platform)
    with pytest.raises(ValidationError, match="revision"):
        platform.ontologies.update_meta(published_wid, "admin", name="改不了")

    wid = _empty_ontology(platform)
    with pytest.raises(ValidationError, match="nothing to update"):
        platform.ontologies.update_meta(wid, "admin")
    with pytest.raises(ValidationError, match="name"):
        platform.ontologies.update_meta(wid, "admin", name="   ")
    with pytest.raises(ValidationError, match="profile"):
        platform.ontologies.update_meta(wid, "admin", profile="fuzzy")


def test_update_meta_requires_editor(platform):
    wid = _empty_ontology(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.ontologies.update_meta(wid, "bob", name="x")
    assert platform.ontologies.get(wid, "admin")["name"] == "编辑测试"


def test_update_meta_over_http(http_server):
    base, published_wid = http_server

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{published_wid}/ontology",
        {"user": "admin", "description": "http 修改"})
    assert status == 400
    assert "revision" in body["error"]

    http_post(base, f"/api/workspaces/{published_wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})
    status, _headers, _body = http_call(
        "PATCH", base, f"/api/workspaces/{published_wid}/ontology",
        {"user": "bob", "description": "x"})
    assert status == 403

    status, ws = http_post(base, "/api/workspaces",
                           {"user": "admin", "name": "draft-ws"})
    assert status == 200
    wid = ws["id"]
    status, _ = http_post(base, f"/api/workspaces/{wid}/ontology",
                          {"user": "admin", "name": "draft-onto"})
    assert status == 200
    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/ontology",
        {"user": "admin", "name": "  新名字  ", "profile": "open"})
    assert status == 200
    assert body["name"] == "新名字"
    assert body["profile"] == "open"
    assert body["lifecycle"] == "draft"


# ----------------------------------------------------------------------
# 2. discard a revision draft
# ----------------------------------------------------------------------


def test_discard_draft_restores_published_version(platform):
    wid = seed_workspace(platform)
    published = platform.ontologies.get(wid, "admin")
    deployment_before = platform.ctx.repo.get("deployment", wid, "current")
    releases_before = len(platform.ctx.repo.list("release", wid))

    draft = platform.ontologies.revise(wid, "admin")
    assert draft["lifecycle"] == "draft"
    assert draft["parent_version"] == published["version"]
    platform.ontologies.update_meta(wid, "admin", description="草稿里改的")

    result = platform.ontologies.discard_draft(wid, "admin")

    assert result["discarded"] == draft["id"]
    restored = result["restored"]
    assert restored["id"] == published["id"]
    assert restored["lifecycle"] == "published"
    current = platform.ontologies.get(wid, "admin")
    assert current["id"] == published["id"]
    assert current["version"] == published["version"]
    assert current["lifecycle"] == "published"
    assert platform.ctx.repo.get("ontology", wid, draft["id"]) is None

    # neither the deploy pointer nor the runtime moved
    assert platform.ctx.repo.get("deployment", wid, "current") == deployment_before
    assert len(platform.ctx.repo.list("release", wid)) == releases_before
    assert platform.ctx.gateway_for(wid).published is True
    assert "ontology.discard_draft" in _actions(platform, wid)

    # a discarded draft can be revised again
    again = platform.ontologies.revise(wid, "admin")
    assert again["lifecycle"] == "draft"
    assert again["parent_version"] == published["version"]


def test_discard_rejects_non_revision_drafts(platform):
    plain_wid = _empty_ontology(platform)
    with pytest.raises(ValidationError, match="parent"):
        platform.ontologies.discard_draft(plain_wid, "admin")

    published_wid = seed_workspace(platform)
    with pytest.raises(ValidationError, match="only a draft"):
        platform.ontologies.discard_draft(published_wid, "admin")


def test_discard_requires_editor(platform):
    wid = seed_workspace(platform)
    platform.ontologies.revise(wid, "admin")
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.ontologies.discard_draft(wid, "bob")


def test_discard_over_http(http_server):
    base, wid = http_server
    status, draft = http_post(
        base, f"/api/workspaces/{wid}/ontology/revisions", {"user": "admin"})
    assert status == 200 and draft["lifecycle"] == "draft"

    status, body = http_post(
        base, f"/api/workspaces/{wid}/ontology/discard", {"user": "admin"})
    assert status == 200
    assert body["discarded"] == draft["id"]
    assert body["restored"]["lifecycle"] == "published"

    status, current = http_get(base, f"/api/workspaces/{wid}/ontology?user=admin")
    assert status == 200
    assert current["id"] == body["restored"]["id"]
    assert current["lifecycle"] == "published"

    status, _ = http_post(
        base, f"/api/workspaces/{wid}/ontology/discard", {"user": "admin"})
    assert status == 400


def test_lifecycle_restore_transition_is_legal():
    assert transition("deprecated", "restore") == "published"
    with pytest.raises(Exception):
        transition("draft", "restore")


# ----------------------------------------------------------------------
# 3. property deletion protection
# ----------------------------------------------------------------------


def test_property_drop_blocked_by_projection_identity_and_vocabulary(platform):
    wid = seed_workspace(platform)
    platform.ontologies.revise(wid, "admin")

    # the seeded projection maps Vehicle.balance and TollStation.status
    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.upsert_object(
            wid, "admin", "Vehicle",
            properties=[{"name": "plate", "type": "string"}])
    message = str(excinfo.value)
    assert "balance" in message and "projection" in message
    assert "主数据投影" in message

    # vocabulary term '拥堵' names TollStation.status
    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.upsert_object(
            wid, "admin", "TollStation",
            properties=[{"name": "name", "type": "string"}])
    message = str(excinfo.value)
    assert "status" in message and "拥堵" in message

    # declare an identity in the draft, then refuse to drop it
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"},
                    {"name": "balance", "type": "number"}],
        identity=["plate"])
    with pytest.raises(ValidationError, match="identity"):
        platform.ontologies.upsert_object(
            wid, "admin", "Vehicle",
            properties=[{"name": "balance", "type": "number"}],
            identity=[])


def test_property_drop_blocked_by_state_machine_and_application(platform):
    wid = platform.workspaces.create("应用引用", actor="admin")["id"]
    platform.ontologies.create(wid, "调度本体", actor="admin")
    vehicle_props = [
        {"name": "plate", "type": "string", "required": True},
        {"name": "balance", "type": "number"},
        {"name": "status", "type": "string"},
        {"name": "note", "type": "string"},
    ]
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle", properties=vehicle_props,
        identity=["plate"],
        state_machine={"field": "status",
                       "transitions": {"free": ["busy"], "busy": ["free"]}})
    platform.ontologies.publish(wid, "admin")

    app = platform.applications.create(wid, "admin", "调度应用")
    platform.applications.add_query(
        wid, app["id"], "admin", "low", object_type="Vehicle",
        filters={"balance__gte": 10})
    platform.applications.add_view(
        wid, app["id"], "admin", "cards", title="车辆", object_type="Vehicle",
        properties=["note"])
    platform.ontologies.revise(wid, "admin")

    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.upsert_object(
            wid, "admin", "Vehicle",
            properties=[p for p in vehicle_props if p["name"] != "balance"],
            identity=["plate"])
    message = str(excinfo.value)
    assert "balance" in message and "调度应用" in message

    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.upsert_object(
            wid, "admin", "Vehicle",
            properties=[p for p in vehicle_props if p["name"] != "note"],
            identity=["plate"])
    assert "调度应用" in str(excinfo.value)

    with pytest.raises(ValidationError, match="state machine"):
        platform.ontologies.upsert_object(
            wid, "admin", "Vehicle",
            properties=[p for p in vehicle_props if p["name"] != "status"],
            identity=["plate"],
            state_machine={"field": "status", "transitions": {}})


def test_unreferenced_property_can_be_dropped(platform):
    wid = seed_workspace(platform)
    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"},
                    {"name": "balance", "type": "number"},
                    {"name": "note", "type": "string"}])

    result = platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"},
                    {"name": "balance", "type": "number"}])
    assert "note" not in result["objects"]["Vehicle"]["properties"]

    # relations stay editable (description/cardinality) without the guard
    updated = platform.ontologies.upsert_relation(
        wid, "admin", "passes", "Vehicle", "TollStation",
        description="车辆经过收费站", cardinality="many-to-many")
    relation = updated["relations"]["passes"]
    assert relation["description"] == "车辆经过收费站"
    assert relation["cardinality"] == "many-to-many"


def test_property_drop_over_http(http_server, platform):
    base, wid = http_server
    status, draft = http_post(
        base, f"/api/workspaces/{wid}/ontology/revisions", {"user": "admin"})
    assert status == 200

    status, body = http_post(
        base, f"/api/workspaces/{wid}/ontology/objects",
        {"user": "admin", "name": "Vehicle",
         "properties": [{"name": "plate", "type": "string"}]})
    assert status == 400
    assert "balance" in body["error"]
    assert "projection" in body["error"]

    status, body = http_post(
        base, f"/api/workspaces/{wid}/ontology/objects",
        {"user": "admin", "name": "Vehicle",
         "properties": [{"name": "plate", "type": "string"},
                        {"name": "balance", "type": "number"},
                        {"name": "note", "type": "string"}]})
    assert status == 200
    status, body = http_post(
        base, f"/api/workspaces/{wid}/ontology/objects",
        {"user": "admin", "name": "Vehicle",
         "properties": [{"name": "plate", "type": "string"},
                        {"name": "balance", "type": "number"}]})
    assert status == 200
    assert "note" not in body["objects"]["Vehicle"]["properties"]
