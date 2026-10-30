"""Application productization: edit views/queries/meta, clone, chart/map config.

An application is the end-user product.  These tests pin the "edit in place
only while it is a draft, clone to evolve a published one" contract, the
chart/map rendering config (which fields feed the graphics), and the HTTP
surface the console uses.
"""

from __future__ import annotations

import pytest

from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)
from tests.conftest import http_call, http_post


def _audit_actions(platform, wid) -> list[str]:
    return [entry["action"] for entry in platform.ctx.audit.list(wid, limit=500)]


def _app(platform, wid, name="运营台") -> str:
    return platform.applications.create(wid, "admin", name)["id"]


def _app_with_view(platform, wid) -> str:
    app_id = _app(platform, wid)
    platform.applications.add_view(
        wid, app_id, "admin", "vehicles", title="车辆", kind="table",
        object_type="Vehicle", properties=["plate", "balance"])
    return app_id


def _published_app(platform, wid) -> str:
    app_id = _app_with_view(platform, wid)
    platform.applications.add_query(
        wid, app_id, "admin", "low_balance", object_type="Vehicle",
        filters={"balance__lt": 50}, expand=["passes"])
    platform.applications.add_agent(
        wid, app_id, "admin", "watch", goal="盯住低余额车辆")
    platform.applications.attach_capability(
        wid, app_id, "admin", platform.capabilities.list(wid, "admin")[0]["id"])
    platform.objectives.set(wid, app_id, "admin", "success_rate", target=0.9,
                            band=0.05)
    return platform.applications.publish(wid, app_id, "admin")["id"]


# ----------------------------------------------------------------------
# metadata
# ----------------------------------------------------------------------


def test_update_application_name_and_description(platform, workspace_id):
    app_id = _app(platform, workspace_id)

    updated = platform.applications.update(
        workspace_id, app_id, "admin", name="运营驾驶舱",
        description="面向站长的运营视图")

    assert updated["name"] == "运营驾驶舱"
    assert updated["description"] == "面向站长的运营视图"
    assert "application.update" in _audit_actions(platform, workspace_id)


def test_update_application_requires_at_least_one_field(platform, workspace_id):
    app_id = _app(platform, workspace_id)

    with pytest.raises(ValidationError):
        platform.applications.update(workspace_id, app_id, "admin")


def test_update_application_rejects_blank_name(platform, workspace_id):
    app_id = _app(platform, workspace_id)

    with pytest.raises(ValidationError):
        platform.applications.update(workspace_id, app_id, "admin", name="   ")


def test_published_application_cannot_be_edited_in_place(platform, workspace_id):
    app_id = _published_app(platform, workspace_id)

    for call in (
        lambda: platform.applications.update(
            workspace_id, app_id, "admin", name="改名"),
        lambda: platform.applications.update_view(
            workspace_id, app_id, "admin", "vehicles", title="改标题"),
        lambda: platform.applications.update_query(
            workspace_id, app_id, "admin", "low_balance",
            filters={"balance__lt": 10}),
    ):
        with pytest.raises(ValidationError) as excinfo:
            call()
        assert "clone" in str(excinfo.value)


# ----------------------------------------------------------------------
# views + chart/map config
# ----------------------------------------------------------------------


def test_update_view_kind_properties_and_chart_config(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)

    updated = platform.applications.update_view(
        workspace_id, app_id, "admin", "vehicles", kind="chart",
        title="余额对比", properties=["plate", "balance"],
        config={"x": "plate", "y": "balance"})

    view = updated["views"]["vehicles"]
    assert view["kind"] == "chart"
    assert view["config"] == {"x": "plate", "y": "balance"}
    assert "application.update_view" in _audit_actions(platform, workspace_id)


def test_add_view_chart_requires_numeric_y(platform, workspace_id):
    app_id = _app(platform, workspace_id)

    with pytest.raises(ValidationError):
        platform.applications.add_view(
            workspace_id, app_id, "admin", "c", kind="chart",
            object_type="Vehicle", config={"x": "plate"})
    with pytest.raises(ValidationError):
        platform.applications.add_view(
            workspace_id, app_id, "admin", "c", kind="chart",
            object_type="Vehicle", config={"x": "plate", "y": "plate"})
    with pytest.raises(ValidationError):
        platform.applications.add_view(
            workspace_id, app_id, "admin", "c", kind="chart",
            object_type="Vehicle", config={"x": "plate", "y": "missing"})

    accepted = platform.applications.add_view(
        workspace_id, app_id, "admin", "c", kind="chart",
        object_type="Vehicle", config={"x": "plate", "y": "balance"})
    assert accepted["views"]["c"]["config"]["y"] == "balance"


def test_add_map_view_requires_numeric_lat_lng(platform):
    wid = platform.workspaces.create("地图空间", actor="admin")["id"]
    platform.ontologies.create(wid, "geo", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Poi",
        properties=[{"name": "name", "type": "string"},
                    {"name": "lat", "type": "number"},
                    {"name": "lng", "type": "number"},
                    {"name": "label", "type": "string"}])
    platform.ontologies.publish(wid, "admin")
    app_id = _app(platform, wid, "地图")

    with pytest.raises(ValidationError):
        platform.applications.add_view(
            wid, app_id, "admin", "pois", kind="map", object_type="Poi",
            config={"lat": "lat"})
    with pytest.raises(ValidationError):
        platform.applications.add_view(
            wid, app_id, "admin", "pois", kind="map", object_type="Poi",
            config={"lat": "label", "lng": "lng"})

    accepted = platform.applications.add_view(
        wid, app_id, "admin", "pois", kind="map", object_type="Poi",
        properties=["name", "lat", "lng"],
        config={"lat": "lat", "lng": "lng"})
    assert accepted["views"]["pois"]["kind"] == "map"


def test_update_view_rejects_dangling_query_alias(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)

    with pytest.raises(ValidationError):
        platform.applications.update_view(
            workspace_id, app_id, "admin", "vehicles",
            query_alias="missing_query")


def test_update_view_unknown_alias_is_not_found(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)

    with pytest.raises(NotFoundError):
        platform.applications.update_view(
            workspace_id, app_id, "admin", "nope", title="x")


def test_publish_validates_chart_config_against_ontology(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    platform.applications.add_view(
        workspace_id, app_id, "admin", "c", kind="chart",
        object_type="Vehicle", config={"x": "plate", "y": "balance"})

    published = platform.applications.publish(workspace_id, app_id, "admin")
    assert published["views"]["c"]["config"] == {"x": "plate", "y": "balance"}


def test_query_driven_chart_view_uses_query_object_type(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    platform.applications.add_query(
        workspace_id, app_id, "admin", "vehicles", object_type="Vehicle")

    updated = platform.applications.add_view(
        workspace_id, app_id, "admin", "balance_chart", kind="chart",
        query_alias="vehicles", config={"x": "plate", "y": "balance"})

    assert updated["views"]["balance_chart"]["object_type"] == ""
    assert updated["views"]["balance_chart"]["config"]["y"] == "balance"
    published = platform.applications.publish(workspace_id, app_id, "admin")
    assert published["lifecycle"] == "published"


# ----------------------------------------------------------------------
# queries
# ----------------------------------------------------------------------


def test_update_query_filters_and_expand(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    platform.applications.add_query(
        workspace_id, app_id, "admin", "low", object_type="Vehicle",
        filters={"balance__lt": 100})

    updated = platform.applications.update_query(
        workspace_id, app_id, "admin", "low", title="低余额",
        filters={"balance__lt": 10}, expand=["passes"])

    query = updated["queries"]["low"]
    assert query["title"] == "低余额"
    assert query["filters"] == {"balance__lt": 10}
    assert query["expand"] == [{"relation": "passes", "direction": "out"}]
    assert "application.update_query" in _audit_actions(platform, workspace_id)


def test_update_query_rejects_unknown_expand_relation(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    platform.applications.add_query(
        workspace_id, app_id, "admin", "low", object_type="Vehicle")

    with pytest.raises(ValidationError):
        platform.applications.update_query(
            workspace_id, app_id, "admin", "low", expand=["not_a_relation"])


def test_update_query_rejects_bad_direction(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    platform.applications.add_query(
        workspace_id, app_id, "admin", "low", object_type="Vehicle")

    with pytest.raises(ValidationError):
        platform.applications.update_query(
            workspace_id, app_id, "admin", "low",
            expand=[{"relation": "passes", "direction": "sideways"}])


# ----------------------------------------------------------------------
# clone
# ----------------------------------------------------------------------


def test_clone_published_application_into_draft(platform, workspace_id):
    app_id = _published_app(platform, workspace_id)

    clone = platform.applications.clone(workspace_id, app_id, "admin")

    assert clone["id"] != app_id
    assert clone["lifecycle"] == "draft"
    assert clone["name"] == "运营台 副本"
    assert set(clone["views"]) == {"vehicles"}
    assert set(clone["queries"]) == {"low_balance"}
    assert [a["alias"] for a in clone["agents"]] == ["watch"]
    assert clone["capabilities"]
    assert clone["objective"]["metric"] == "success_rate"
    assert "application.clone" in _audit_actions(platform, workspace_id)

    source = platform.applications.get(workspace_id, app_id, "admin")
    assert source["lifecycle"] == "published"
    assert source["name"] == "运营台"


def test_clone_accepts_explicit_name(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)

    clone = platform.applications.clone(
        workspace_id, app_id, "admin", name="新驾驶舱")

    assert clone["name"] == "新驾驶舱"
    assert clone["lifecycle"] == "draft"


def test_cloned_application_can_be_published(platform, workspace_id):
    app_id = _published_app(platform, workspace_id)
    clone = platform.applications.clone(workspace_id, app_id, "admin")

    published = platform.applications.publish(
        workspace_id, clone["id"], "admin")

    assert published["lifecycle"] == "published"
    assert platform.applications.get(workspace_id, app_id, "admin")[
        "lifecycle"] == "published"


# ----------------------------------------------------------------------
# permissions + HTTP
# ----------------------------------------------------------------------


def test_viewer_cannot_edit_application(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)
    platform.workspaces.add_member(workspace_id, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.applications.update(workspace_id, app_id, "bob", name="x")
    with pytest.raises(AccessDeniedError):
        platform.applications.update_view(
            workspace_id, app_id, "bob", "vehicles", title="x")
    with pytest.raises(AccessDeniedError):
        platform.applications.clone(workspace_id, app_id, "bob")


def test_http_application_edit_and_clone(http_server):
    base, wid = http_server
    created = http_post(base, f"/api/workspaces/{wid}/applications",
                        {"user": "admin", "name": "HTTP 应用"})
    assert created[0] == 200
    aid = created[1]["id"]

    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/views",
        {"user": "admin", "alias": "vehicles", "kind": "table",
         "object_type": "Vehicle", "properties": ["plate", "balance"]})
    assert status == 200

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/applications/{aid}",
        {"user": "admin", "name": "HTTP 应用 2"})
    assert status == 200 and body["name"] == "HTTP 应用 2"

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/applications/{aid}/views/vehicles",
        {"user": "admin", "kind": "chart",
         "config": {"x": "plate", "y": "balance"}})
    assert status == 200
    assert body["views"]["vehicles"]["kind"] == "chart"

    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/clone",
        {"user": "admin", "name": "HTTP 副本"})
    assert status == 200 and body["lifecycle"] == "draft"
    assert body["name"] == "HTTP 副本"

    # viewer is rejected over HTTP too
    http_post(base, f"/api/workspaces/{wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})
    status, _headers, _body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/applications/{aid}",
        {"user": "bob", "name": "偷偷改名"})
    assert status in (401, 403)
    status, _body = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/clone",
        {"user": "bob"})
    assert status in (401, 403)

    # invalid chart config is a clean 400, not a crash
    status, _body = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/views",
        {"user": "admin", "alias": "bad", "kind": "chart",
         "object_type": "Vehicle", "config": {"x": "plate"}})
    assert status == 400
