"""Smart-UI bridge: platform ontology/data → deterministic program + payloads."""

from __future__ import annotations

import time

import pytest

from onto_platform.application.smartui.studio import (
    _get as _generation_row,
    get_generation,
    start_generation,
)
from onto_platform.errors import PlatformError, ValidationError
from onto_platform.interfaces.smart_ui import (
    compile_smart_ui,
    compile_smart_ui_blueprint,
    invoke_smart_action,
    smart_ui_meta,
)
from tests.conftest import http_get, http_post

SCAN_SOURCE = """
def execute(ctx):
    station = ctx.object()
    return {"status": station.get("status"), "station": station.ref}
"""

CHECK_SOURCE = """
def execute(ctx):
    site = ctx.object()
    return {"status": site.get("status"), "site": site.ref}
"""


def _seed_generic_workspace(platform) -> str:
    """A non-toll ontology: devices, sites, no plate anywhere."""
    wid = platform.workspaces.create("设备运维", actor="admin")["id"]
    platform.ontologies.create(wid, "inventory", actor="admin",
                               description="设备台账本体")
    platform.ontologies.upsert_object(
        wid, "admin", "Device",
        properties=[
            {"name": "device_id", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "status", "type": "string"},
            {"name": "temperature", "type": "number"},
            {"name": "last_seen", "type": "string"},
            {"name": "longitude", "type": "number"},
            {"name": "latitude", "type": "number"},
        ],
        description="设备")
    platform.ontologies.upsert_object(
        wid, "admin", "Site",
        properties=[
            {"name": "site_code", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "status", "type": "string"},
        ],
        description="站点")
    platform.ontologies.upsert_relation(wid, "admin", "located_at", "Device", "Site")
    platform.ontologies.publish(wid, "admin")

    source = platform.data.connect_source(
        wid, "admin", "设备库", "static",
        assets=[
            {"name": "devices", "config": {"rows": [
                {"did": "DEV-001", "name": "冷机 A", "status": "warning",
                 "temperature": 8.4, "seen": "2026-02-01T08:00:00+00:00",
                 "lon": 116.31, "lat": 39.91, "sid": "SITE-1"},
                {"did": "DEV-002", "name": "冷机 B", "status": "normal",
                 "temperature": 4.2, "seen": "2026-02-02T09:00:00+00:00",
                 "lon": 116.52, "lat": 40.12, "sid": "SITE-2"},
            ]}},
            {"name": "sites", "config": {"rows": [
                {"sid": "SITE-1", "name": "东区机房", "status": "warning"},
                {"sid": "SITE-2", "name": "西区机房", "status": "normal"},
            ]}},
        ])
    projection = platform.data.create_projection(
        wid, "admin", "设备投影", source["id"],
        object_mappings=[
            {"asset": "devices", "object_type": "Device", "id_field": "did",
             "fields": [
                 {"source_field": "name", "property": "name"},
                 {"source_field": "status", "property": "status"},
                 {"source_field": "temperature", "property": "temperature"},
                 {"source_field": "seen", "property": "last_seen"},
                 {"source_field": "lon", "property": "longitude"},
                 {"source_field": "lat", "property": "latitude"},
             ]},
            {"asset": "sites", "object_type": "Site", "id_field": "sid",
             "fields": [
                 {"source_field": "name", "property": "name"},
                 {"source_field": "status", "property": "status"},
             ]},
        ],
        relation_mappings=[
            {"asset": "devices", "relation": "located_at",
             "source_field": "did", "target_field": "sid"},
        ])
    platform.data.run_projection(wid, projection["id"], "admin")
    capability = platform.capabilities.create(
        wid, "admin", key="site_check", name="站点巡检", target_type="Site",
        description="巡检站点状态", inputs=[],
        implementation={"kind": "python", "source": CHECK_SOURCE})
    platform.capabilities.publish(wid, capability["id"], "admin")
    return wid


def _engine_available() -> bool:
    from onto_platform.interfaces import smart_ui

    try:
        smart_ui._engine()
    except ValidationError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _engine_available(),
    reason="vibe-可视化engine source checkout is required for smart-ui tests",
)


def _add_scan_capability(platform, wid: str):
    capability = platform.capabilities.create(
        wid, "admin", key="congestion_scan", name="拥堵巡检",
        target_type="TollStation", description="巡检站点状态",
        inputs=[], implementation={"kind": "python", "source": SCAN_SOURCE})
    platform.capabilities.publish(wid, capability["id"], "admin")


def test_meta_uses_the_current_workspace(platform, workspace_id):
    meta = smart_ui_meta(platform, workspace_id, "admin")
    assert meta["workspace"]["name"] == "高速运营"
    assert meta["counts"]["Vehicle"] == 2
    assert meta["counts"]["TollStation"] == 2
    assert meta["bridge"]["focusObject"] == "Vehicle"
    assert "鲁A12345" in meta["defaultQuestion"] or "鲁B67890" in meta["defaultQuestion"]


def test_compile_builds_program_and_payloads_from_real_state(platform, workspace_id):
    meta = smart_ui_meta(platform, workspace_id, "admin")
    result = compile_smart_ui(
        platform, workspace_id, "admin", meta["defaultQuestion"], ["owner"])
    compiled = result["compiled"]
    bundle = result["bundle"]

    assert compiled["ok"] is True
    assert compiled["program"]
    assert "view_identity" in compiled["program"]
    assert {view["perspective"] for view in compiled["viewMap"]} >= {
        "identity", "relationship", "state", "rule",
    }
    assert bundle["identity"]["plate"] in {"鲁A12345", "鲁B67890"}
    assert bundle["viewPayloads"]["view_identity"]["rows"][0]["plate"] == (
        bundle["identity"]["plate"])
    assert bundle["workspace"]["id"] == workspace_id
    # The fixture publishes recharge with a declared ``amount`` input; it is now
    # a first-class action whose inputs are collected by the runtime.
    assert compiled["permissions"]["allowed"] == ["recharge"]
    action = next(
        item for item in compiled["permissions"]["decisions"]
        if item["actionId"] == "recharge"
    )
    assert action["parameters"][0]["name"] == "amount"
    assert "Mutation(\"recharge\"" in compiled["program"]
    assert "amount: 0" in compiled["program"]
    assert "ActionButton" in compiled["program"]


def test_generic_ontology_without_plate_compiles_and_renders(platform):
    wid = _seed_generic_workspace(platform)
    meta = smart_ui_meta(platform, wid, "admin")
    assert meta["ready"] is True
    assert meta["bridge"]["focusObject"] == "Device"
    assert meta["bridge"]["focusLabel"] == "设备"
    assert meta["bridge"]["identityKey"] == "device_id"
    assert meta["bridge"]["focusValue"]
    assert meta["bridge"]["stationObject"] == "Site"
    assert meta["counts"]["Device"] == 2

    result = compile_smart_ui(
        platform, wid, "admin",
        f"{meta['bridge']['focusValue']} 为什么异常？", ["owner"])
    compiled = result["compiled"]
    bundle = result["bundle"]
    assert compiled["ok"] is True
    assert "view_identity" in compiled["program"]
    identity = bundle["identity"]
    assert identity["objectType"] == "Device"
    assert identity["objectLabel"] == "设备"
    assert identity["plate"] in {"冷机 A", "冷机 B"}
    assert any(fact["label"] == "温度" for fact in identity["facts"])
    node_types = {node["type"] for node in bundle["relationship"]["nodes"]}
    assert "Device" in node_types and "Site" in node_types
    assert bundle["statistics"]["seriesLabel"] == "温度趋势"
    # A geo ontology must answer the location capability instead of compiling
    # a view whose payload is missing.
    assert "location" in {view["perspective"] for view in compiled["viewMap"]}
    location = bundle["viewPayloads"]["view_location"]["rows"][0]
    assert location["mode"] == "points"
    assert len(location["points"]) >= 1
    assert {"lng", "lat", "label"} <= set(location["points"][0])
    # Default location stays the canonical map; the spatial variants are
    # opt-in through the adjudicated display option.
    assert location["viewType"] == "map"
    assert location["display"] == "map"
    assert location["style"] == {}
    assert location["points"][0].get("value") is not None

    action = invoke_smart_action(
        platform, wid, "admin", "site_check",
        {"focusValue": meta["bridge"]["focusValue"], "role": "owner"})
    assert action["ok"] is True
    assert action["target"].startswith("Site/")
    assert action["result"]["status"] == "succeeded"
    assert action["plate"] == meta["bridge"]["focusValue"]


def test_spatial_location_variants_compile_and_carry_style(platform):
    wid = _seed_generic_workspace(platform)
    meta = smart_ui_meta(platform, wid, "admin")
    question = f"{meta['bridge']['focusValue']} 为什么异常？"
    roles = ["owner"]
    base = compile_smart_ui(platform, wid, "admin", question, roles)
    blueprint = base["compiled"]["blueprint"]
    for display, component, view_type, extra in (
        ("gis", "GISMap", "gis",
         {"basemap": "terrain", "layers": ["route", "points"], "cluster": False}),
        ("scene25d", "Scene25D", "scene_2_5d",
         {"heightField": "temperature"}),
        ("twin3d", "Twin3D", "twin_3d",
         {"heightField": "temperature", "theme": "dark"}),
    ):
        candidate = {
            **blueprint,
            "views": [dict(view) for view in blueprint["views"]],
        }
        candidate["views"] = [
            {**view,
             "options": {**(view.get("options") or {}), "display": display,
                         **extra}}
            if view.get("perspective") == "location" else view
            for view in candidate["views"]
        ]
        result = compile_smart_ui_blueprint(
            platform, wid, "admin", question, roles, candidate)
        assert result["compiled"]["ok"] is True
        assert f"{component}(" in result["compiled"]["program"]
        location = result["bundle"]["viewPayloads"]["view_location"]["rows"][0]
        assert location["viewType"] == view_type
        assert location["display"] == display
        for key, value in extra.items():
            assert location["style"][key] == value
        if "heightField" in extra:
            point = location["points"][0]
            assert point["field"] == "temperature"
            assert isinstance(point["value"], (int, float))


def test_digital_twin_pack_preset_recompiles_to_gis_component(platform):
    wid = _seed_generic_workspace(platform)
    started = start_generation(
        platform, wid, "admin", "查看设备轨迹", ["owner"],
        mode="deterministic", pack_id="onto.pack.digital-twin")
    deadline = time.time() + 8.0
    row: dict = {}
    while time.time() < deadline:
        row = get_generation(platform, wid, "admin", started["id"])
        if row.get("status") in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.02)
    assert row["status"] == "succeeded"
    assert any(view.get("perspective") == "location"
               and (view.get("options") or {}).get("display") == "gis"
               for view in row["blueprint"]["views"])
    # The program must be compiled from the preset-applied blueprint, not the
    # planner's pre-preset default (which would still call GeoMap).
    assert "GISMap(" in _generation_row(
        platform, wid, started["id"])["program"]


def test_status_normalization_understands_chinese_labels():
    from onto_platform.interfaces.smart_ui import _status_is_bad, _status_risk, _status_text

    assert _status_risk("异常") == "HIGH"
    assert _status_risk("停用") == "HIGH"
    assert _status_risk("高风险") == "HIGH"
    assert _status_risk("中风险") == "MEDIUM"
    assert _status_risk("正常") == "LOW"
    # A non-status word containing 中 must not be read as MEDIUM.
    assert _status_risk("中国") == "LOW"
    assert _status_is_bad("异常") is True
    assert _status_is_bad("正常") is False
    assert _status_text("异常") == "异常"


def test_action_rejects_non_empty_args_instead_of_dropping_them(platform):
    wid = _seed_generic_workspace(platform)
    with pytest.raises(ValidationError):
        invoke_smart_action(
            platform, wid, "admin", "site_check",
            {"focusValue": "冷机 A", "role": "owner", "args": {"amount": 3}})


def test_action_uses_requested_focus_value_for_target_resolution(platform):
    wid = _seed_generic_workspace(platform)
    meta = smart_ui_meta(platform, wid, "admin")
    other_value = "冷机 A" if meta["bridge"]["focusValue"] != "冷机 A" else "冷机 B"
    result = invoke_smart_action(
        platform, wid, "admin", "site_check",
        {"focusValue": other_value, "role": "owner"})
    assert result["ok"] is True
    assert result["focusValue"] == other_value
    assert result["target"] == "Site/SITE-1"
    assert result["result"]["output"]["site"] == "Site/SITE-1"


def test_identity_key_prefers_own_id_over_foreign_keys():
    from onto_platform.interfaces.smart_ui import _semantic_keys

    spec = {
        "name": "TollTransaction",
        "properties": {
            "lane_id": {"type": "string"},
            "transaction_id": {"type": "string"},
            "plate": {"type": "string"},
            "fee": {"type": "number"},
        },
    }
    keys = _semantic_keys(spec, [{
        "id": "T-1",
        "properties": {"lane_id": "L-1", "transaction_id": "T-1",
                       "plate": "鲁A12345", "fee": 12.5},
    }])
    assert keys["idKey"] == "transaction_id"
    assert keys["groupKey"] == "plate"


def test_compile_rejects_empty_workspace(platform, empty_workspace):
    with pytest.raises(PlatformError):
        smart_ui_meta(platform, empty_workspace, "admin")


def test_meta_guides_empty_workspace_without_records(platform):
    wid = platform.workspaces.create("空数据", actor="admin")["id"]
    platform.ontologies.create(wid, "traffic", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle", properties=[{"name": "plate", "type": "string"}])
    platform.ontologies.publish(wid, "admin")
    meta = smart_ui_meta(platform, wid, "admin")
    assert meta["ready"] is False
    assert meta["bridge"]["focusObject"] is None
    assert "数据源" in meta["reason"] or "投影" in meta["reason"]
    assert meta["guidance"]
    with pytest.raises(ValidationError):
        compile_smart_ui(platform, wid, "admin", "为什么异常？", ["owner"])


def test_compile_rejects_unknown_role(platform, workspace_id):
    with pytest.raises(ValidationError):
        compile_smart_ui(platform, workspace_id, "admin", "鲁A12345 为什么异常？", [])


def test_viewer_cannot_request_owner_perspective(platform, workspace_id):
    from onto_platform.errors import AccessDeniedError

    platform.workspaces.add_member(workspace_id, "bob", "viewer", "admin")
    meta = smart_ui_meta(platform, workspace_id, "bob")
    assert meta["actorRole"] == "viewer"
    assert [role["id"] for role in meta["roles"]] == ["viewer"]
    with pytest.raises(AccessDeniedError):
        compile_smart_ui(
            platform, workspace_id, "bob", "鲁A12345 为什么异常？", ["owner"])

    _add_scan_capability(platform, workspace_id)
    with pytest.raises(AccessDeniedError):
        invoke_smart_action(
            platform, workspace_id, "bob", "congestion_scan",
            {"plate": "鲁A12345", "role": "owner"})


def test_http_meta_compile_and_action(http_server, platform):
    base, wid = http_server
    _add_scan_capability(platform, wid)

    status, meta = http_get(base, f"/api/workspaces/{wid}/smart-ui/meta")
    assert status == 200
    assert meta["bridge"]["stationObject"] == "TollStation"

    status, compiled = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/compile",
        {"question": meta["defaultQuestion"], "roles": ["owner"]})
    assert status == 200
    assert compiled["compiled"]["ok"] is True
    plate = compiled["bundle"]["pass"]["plate"]
    assert plate in {"鲁A12345", "鲁B67890"}

    status, action = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/actions/congestion_scan",
        {"plate": plate, "role": "owner"})
    assert status == 200
    assert action["ok"] is True
    assert action["target"].startswith("TollStation/")
    assert action["result"]["status"] == "succeeded"

    # Parameterized capability over the HTTP door: the runtime posts typed args.
    status, recharge = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/actions/recharge",
        {"plate": plate, "role": "owner", "args": {"amount": 50}})
    assert status == 200
    assert recharge["ok"] is True
    assert recharge["args"] == {"amount": 50.0}
    assert recharge["result"]["output"]["balance"] >= 50


def test_action_requires_a_published_platform_capability(platform, workspace_id):
    with pytest.raises(ValidationError):
        invoke_smart_action(
            platform, workspace_id, "admin", "not_a_capability",
            {"plate": "鲁A12345", "role": "owner"})


def test_action_requires_parameterized_capability_args(platform, workspace_id):
    # ``recharge`` is published by the fixture but requires an ``amount``.
    with pytest.raises(ValidationError):
        invoke_smart_action(
            platform, workspace_id, "admin", "recharge",
            {"plate": "鲁A12345", "role": "owner"})


def test_action_collects_typed_args_for_parameterized_capability(
        platform, workspace_id):
    result = invoke_smart_action(
        platform, workspace_id, "admin", "recharge",
        {"plate": "鲁A12345", "role": "owner", "args": {"amount": "100"}})
    assert result["ok"] is True
    assert result["args"] == {"amount": 100.0}
    assert result["result"]["output"]["balance"] == 200.0


def test_action_rejects_unknown_or_mistyped_parameterized_args(
        platform, workspace_id):
    for args in ({"amount": "abc"}, {"amount": 1, "extra": 2}, {"amount": ""}):
        with pytest.raises(ValidationError):
            invoke_smart_action(
                platform, workspace_id, "admin", "recharge",
                {"plate": "鲁A12345", "role": "owner", "args": args})


def test_action_rejects_viewer_when_role_is_restricted(platform, workspace_id):
    _add_scan_capability(platform, workspace_id)
    from onto_platform.errors import AccessDeniedError

    with pytest.raises(AccessDeniedError):
        invoke_smart_action(
            platform, workspace_id, "admin", "congestion_scan",
            {"plate": "鲁A12345", "role": "viewer"})


def test_http_compile_returns_validation_error_for_unknown_workspace(
        http_server):
    base, _wid = http_server
    status, body = http_post(
        base, "/api/workspaces/no-such-workspace/smart-ui/compile",
        {"question": "鲁A12345 为什么异常？", "roles": ["owner"]})
    assert status >= 400
    assert body["error"]
