"""Runtime contributions, pack binding, negotiation and replay (Phase 1+)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from onto_platform.application.smartui.builtin import build_default_host
from onto_platform.application.smartui.contracts import (
    HOST_API_VERSION,
    PluginManifest,
)
from onto_platform.application.smartui.external import (
    ExternalPluginError,
    install_external_plugin,
    sign_package,
)
from onto_platform.application.smartui.host import PluginHost
from onto_platform.application.smartui.packs import (
    WorkspacePack,
    apply_view_presets,
    set_workspace_pack,
    workspace_pack_id,
)
from onto_platform.application.smartui.studio import (
    get_generation,
    replay_generation,
    start_generation,
)
from onto_platform.errors import NotFoundError, ValidationError
from onto_platform.interfaces.smart_ui import smart_ui_meta
from tests.conftest import http_get, http_post


def _wait(platform, workspace_id: str, generation_id: str,
          timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = get_generation(platform, workspace_id, "admin", generation_id)
        if row.get("status") in {"succeeded", "failed", "cancelled"}:
            return row
        time.sleep(0.02)
    raise AssertionError("generation did not settle")


def test_every_builtin_manifest_has_a_runtime_implementation(platform):
    missing = [item["id"] for item in platform.smartui.summary()
               if not item["implemented"]]
    assert missing == []
    assert platform.smartui.implementation(
        "onto.smartui.context.default").id == "onto.smartui.context.default"
    assert platform.smartui.implementation(
        "onto.smartui.publisher.default").id == "onto.smartui.publisher.default"
    assert platform.smartui.implementation(
        "onto.smartui.ui.aiview").manifest()["slots"][0]["slot"] == "aiview.tabs"


def test_meta_exposes_ui_slots_engine_descriptors_and_active_pack(
        platform, workspace_id):
    meta = smart_ui_meta(platform, workspace_id, "admin")
    assert meta["activePack"] is None
    assert {item["id"] for item in meta["ui"]["slots"]} == {"studio", "history"}
    assert meta["ui"]["remoteScripts"] is False
    renderers = {item["viewType"]: item for item in meta["ui"]["renderers"]}
    assert renderers["table"]["component"] == "DataTable"
    assert renderers["table"]["pluginId"] == "onto.smartui.renderer.table"
    assert renderers["chart"]["format"] == "chart"
    assert renderers["gis"]["component"] == "GISMap"
    assert renderers["scene_2_5d"]["format"] == "scene25d"
    assert renderers["twin_3d"]["pluginId"] == "onto.smartui.renderer.twin3d"
    descriptors = {item["viewType"]: item for item in meta["engineDescriptors"]}
    assert descriptors["table"]["component"] == "DataTable"
    assert descriptors["chart"]["perspective"] == "statistics"
    assert descriptors["gis"]["perspective"] == "location"
    assert descriptors["scene_2_5d"]["component"] == "Scene25D"
    assert descriptors["twin_3d"]["component"] == "Twin3D"
    assert meta["pluginLock"] == platform.smartui.execution_lock_dicts()
    assert meta["plugins"]


def test_permission_and_execution_lock_are_enforced():
    host = build_default_host()
    with pytest.raises(ValidationError):
        host.implementation("onto.smartui.generator.llm",
                            permission="artifact.publish")
    empty = PluginHost()
    empty.register(PluginManifest(id="onto.test.context", kind="context"))
    assert empty.lock_dicts() == [{
        "id": "onto.test.context", "kind": "context", "version": "1.0.0",
        "apiVersion": "smartui/v1"}]
    assert empty.execution_lock_dicts() == []
    with pytest.raises(ValidationError):
        empty.implementation("onto.test.context", permission="context.read")


def test_spatial_view_plugins_register_and_adjudicate_options():
    host = build_default_host()
    records = {record.manifest.view_type: record for record in
               host.find("view", perspective="location",
                         permission="view.render")}
    assert set(records) == {"gis", "scene_2_5d", "twin_3d"}

    obj = SimpleNamespace(properties=[
        SimpleNamespace(name="longitude", is_numeric=True),
        SimpleNamespace(name="latitude", is_numeric=True),
        SimpleNamespace(name="speed", is_numeric=True),
        SimpleNamespace(name="station", is_numeric=False),
    ])
    scene = records["scene_2_5d"].implementation.adjudicate_options(
        {"display": "scene25d", "heightField": "speed",
         "colorField": "station", "showLabels": False},
        obj, "location", [])
    assert scene == {"display": "scene25d", "heightField": "speed",
                     "colorField": "station", "showLabels": False}

    scene_pruned: list[dict] = []
    scene = records["scene_2_5d"].implementation.adjudicate_options(
        {"heightField": "station"}, obj, "location", scene_pruned)
    assert "heightField" not in scene
    assert {item["key"] for item in scene_pruned} == {"heightField"}

    gis_pruned: list[dict] = []
    gis = records["gis"].implementation.adjudicate_options(
        {"display": "gis", "basemap": "terrain", "layers": ["route", "bad"],
         "cluster": True, "heightField": "speed"},
        obj, "location", gis_pruned)
    assert gis["display"] == "gis"
    assert gis["basemap"] == "terrain"
    assert gis["layers"] == ["route"]
    assert gis["cluster"] is True
    assert "heightField" not in gis
    assert {item["key"] for item in gis_pruned} == set()

    # A focus object without its own geo props can still get a location view
    # through related objects; the plugin must not drop the display choice.
    vehicle = SimpleNamespace(properties=[
        SimpleNamespace(name="speed", is_numeric=True)])
    related_geo = records["gis"].implementation.adjudicate_options(
        {"display": "gis", "basemap": "night"}, vehicle, "location", [])
    assert related_geo == {"display": "gis", "basemap": "night"}


def test_api_version_range_is_negotiated_and_incompatible_is_rejected():
    host = PluginHost()
    manifest = PluginManifest(
        id="onto.test.view.ranged", kind="view",
        api_versions=(">=smartui/v1 <smartui/v2",))
    host.register(manifest, SimpleNamespace())
    assert manifest.compatible_with(HOST_API_VERSION) is True

    future = PluginManifest(
        id="onto.test.view.future", kind="view",
        api_version="smartui/v2",
        api_versions=(">=smartui/v2",))
    assert future.compatible_with(HOST_API_VERSION) is False
    with pytest.raises(ValidationError):
        host.register(future, SimpleNamespace())


def test_untrusted_external_manifest_is_rejected():
    host = build_default_host()
    with pytest.raises(ValidationError):
        host.register(PluginManifest(
            id="onto.test.ui.untrusted", kind="ui", builtin=False,
            permissions=("ui.slot",)), SimpleNamespace())


def test_workspace_pack_binding_changes_meta_and_generation(
        platform, workspace_id):
    with pytest.raises(NotFoundError):
        set_workspace_pack(platform, workspace_id, "admin", "onto.pack.missing")

    config = set_workspace_pack(
        platform, workspace_id, "admin", "onto.pack.traffic")
    assert config["packId"] == "onto.pack.traffic"
    assert workspace_pack_id(platform, workspace_id, "admin") == "onto.pack.traffic"
    meta = smart_ui_meta(platform, workspace_id, "admin")
    assert meta["activePack"]["id"] == "onto.pack.traffic"
    assert meta["defaultQuestion"] == "查看车辆当前情况并按站点对比"

    started = start_generation(
        platform, workspace_id, "admin", "查看车辆当前情况", ["owner"],
        mode="deterministic")
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded"
    assert row["packId"] == "onto.pack.traffic"
    assert row["trace"]["workspacePack"]["id"] == "onto.pack.traffic"
    assert row["context"]["workspacePack"]["id"] == "onto.pack.traffic"

    # A request override wins for this generation only.
    override = start_generation(
        platform, workspace_id, "admin", "查看车辆当前情况", ["owner"],
        mode="deterministic", pack_id="onto.pack.finance-lease")
    overridden = _wait(platform, workspace_id, override["id"])
    assert overridden["packId"] == "onto.pack.finance-lease"


def test_pack_with_non_prompt_binding_is_rejected(platform, workspace_id):
    platform.smartui.register_pack(WorkspacePack(
        id="onto.test.pack.bad",
        name="坏包",
        prompt_pack="onto.smartui.generator.llm",
    ))
    with pytest.raises(ValidationError):
        set_workspace_pack(
            platform, workspace_id, "admin", "onto.test.pack.bad")


def test_pack_presets_are_defaults_and_model_options_win():
    host = build_default_host()
    pack = host.pack("onto.pack.traffic")
    blueprint = apply_view_presets({"views": [
        {"perspective": "statistics", "options": {"limit": 5}},
        {"perspective": "identity"},
    ]}, pack)
    assert blueprint["views"][0]["options"]["chartType"] == "bar"
    assert blueprint["views"][0]["options"]["limit"] == 5
    assert blueprint["views"][1].get("options") is None


def test_replay_compiles_blueprint_and_reports_plugin_lock_drift(
        platform, workspace_id):
    started = start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"],
        mode="deterministic")
    row = _wait(platform, workspace_id, started["id"])
    replay = replay_generation(platform, workspace_id, "admin", row["id"])
    assert replay["replay"]["ok"] is True
    assert replay["replay"]["missing"] == []

    platform.smartui.disable("onto.smartui.view.table")
    try:
        drifted = replay_generation(platform, workspace_id, "admin", row["id"])
        assert drifted["replay"]["ok"] is False
        assert "onto.smartui.view.table" in drifted["replay"]["missing"]
    finally:
        platform.smartui.enable("onto.smartui.view.table")


def test_external_package_requires_flag_secret_and_signature(tmp_path, monkeypatch):
    manifest = {
        "id": "onto.test.ui.external",
        "kind": "ui",
        "version": "1.0.0",
        "apiVersion": "smartui/v1",
        "builtin": False,
        "permissions": ["ui.slot"],
        "summary": "external test",
    }
    source = (
        "class Plugin:\n"
        "    def manifest(self):\n"
        "        return {'slots': [], 'renderers': [], 'remoteScripts': False}\n"
        "def create_plugin():\n"
        "    return Plugin()\n"
    )
    (tmp_path / "manifest.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8")
    (tmp_path / "plugin.py").write_text(source, encoding="utf-8")
    signature = sign_package(
        manifest, (tmp_path / "plugin.py").read_bytes(), "test-secret")

    host = build_default_host()
    with pytest.raises(ExternalPluginError):
        install_external_plugin(host, tmp_path, signature)

    monkeypatch.setenv("ONTO_SMARTUI_EXTERNAL_PLUGINS", "1")
    monkeypatch.setenv("ONTO_SMARTUI_PLUGIN_SECRET", "test-secret")
    with pytest.raises(ExternalPluginError):
        install_external_plugin(host, tmp_path, "bad-signature")
    installed = install_external_plugin(host, tmp_path, signature)
    assert installed["id"] == "onto.test.ui.external"
    assert host.has("onto.test.ui.external")


def test_smart_ui_config_api_binds_pack(http_server, platform):
    base, workspace_id = http_server
    status, body = http_post(
        base, f"/api/workspaces/{workspace_id}/smart-ui/config",
        {"pack_id": "onto.pack.traffic"})
    assert status == 200
    assert body["packId"] == "onto.pack.traffic"

    status, config = http_get(
        base, f"/api/workspaces/{workspace_id}/smart-ui/config")
    assert status == 200
    assert config["pack"]["name"] == "高速运营"

    status, generation = http_post(
        base, f"/api/workspaces/{workspace_id}/smart-ui/generations",
        {"question": "查看车辆情况", "roles": ["owner"],
         "mode": "deterministic"})
    assert status == 202
    row = _wait(platform, workspace_id, generation["id"])
    assert row["packId"] == "onto.pack.traffic"

    status, replay = http_get(
        base,
        f"/api/workspaces/{workspace_id}/smart-ui/generations/"
        f"{generation['id']}/replay")
    assert status == 200
    assert replay["replay"]["ok"] is True
