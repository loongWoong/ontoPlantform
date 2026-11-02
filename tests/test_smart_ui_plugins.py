"""Smart-UI plugin host contracts and composition (Phase 0)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from onto_platform.application.smartui.adjudication import (
    AdjudicationContext,
    SanitizerAdjudicator,
)
from onto_platform.application.smartui.builtin import BUILTIN_MANIFESTS, build_default_host
from onto_platform.application.smartui.contracts import PluginManifest
from onto_platform.application.smartui.host import PluginHost
from onto_platform.application.smartui.packs import (
    BUILTIN_PACKS,
    WorkspacePack,
    load_pack,
)
from onto_platform.application.smartui.prompt import PromptRequest
from onto_platform.application.smartui.studio import (
    _normalize_view_options,
    accept_generation,
    get_generation,
    start_generation,
)
from onto_platform.errors import NotFoundError, ValidationError
from onto_platform.interfaces.smart_ui import smart_ui_meta


def test_manifest_rejects_bad_identity_and_api_version():
    with pytest.raises(ValidationError):
        PluginManifest(id="Not A Plugin", kind="view")
    with pytest.raises(ValidationError):
        PluginManifest(id="onto.test.view", kind="unknown")
    with pytest.raises(ValidationError):
        PluginManifest(id="onto.test.view", kind="view", version="v1")
    with pytest.raises(ValidationError):
        PluginManifest(id="onto.test.view", kind="view",
                       api_version="smartui/v99")
    with pytest.raises(ValidationError):
        PluginManifest(id="onto.test.renderer.empty", kind="renderer")


def test_host_registers_looks_up_and_rejects_duplicates():
    host = PluginHost()
    manifest = PluginManifest(id="onto.test.generator", kind="generator",
                              summary="test")
    record = host.register(manifest, object())
    assert host.get(manifest.id) is record
    assert host.implementation(manifest.id) is record.implementation
    with pytest.raises(ValidationError):
        host.register(manifest)
    with pytest.raises(NotFoundError):
        host.get("onto.test.missing")


def test_host_lock_is_sorted_and_respects_enabled_state():
    host = PluginHost()
    host.register(PluginManifest(id="onto.test.view.b", kind="view"))
    host.register(PluginManifest(id="onto.test.generator.a", kind="generator"))
    host.register(PluginManifest(id="onto.test.view.a", kind="view"))
    assert [item.id for item in host.lock()] == [
        "onto.test.generator.a", "onto.test.view.a", "onto.test.view.b"]
    host.disable("onto.test.view.a")
    assert [item.id for item in host.lock()] == [
        "onto.test.generator.a", "onto.test.view.b"]
    assert next(item for item in host.summary()
               if item["id"] == "onto.test.view.a")["enabled"] is False


def test_builtin_host_exposes_expected_contribution_kinds():
    host = build_default_host()
    kinds = {record.manifest.kind for record in host.find(enabled_only=True)}
    assert {"context", "generator", "prompt", "adjudicator",
            "view", "renderer", "publisher", "ui"} <= kinds
    lock = host.lock_dicts()
    assert lock == sorted(lock, key=lambda item: (item["kind"], item["id"]))
    assert len({item["id"] for item in lock}) == len(lock)


def test_renderer_registry_is_versioned_and_follows_view_lifecycle():
    host = build_default_host()
    renderers = {item["viewType"]: item for item in host.renderer_dicts()}
    assert renderers["table"]["component"] == "DataTable"
    assert renderers["table"]["pluginId"] == "onto.smartui.renderer.table"
    assert renderers["chart"]["pluginId"] == "onto.smartui.renderer.chart"
    assert renderers["gis"]["component"] == "GISMap"
    assert renderers["scene_2_5d"]["component"] == "Scene25D"
    assert renderers["twin_3d"]["component"] == "Twin3D"
    assert {item["viewType"] for item in host.ui_manifest()["renderers"]} >= {
        "table", "chart", "gis", "scene_2_5d", "twin_3d"}
    host.disable("onto.smartui.renderer.table")
    assert host.renderer("table") is None
    assert "table" not in {
        item["viewType"] for item in host.ui_manifest()["renderers"]}
    host.enable("onto.smartui.renderer.table")
    assert host.renderer("table")["component"] == "DataTable"

    with pytest.raises(ValidationError):
        host.register(PluginManifest(
            id="onto.test.renderer.duplicate", kind="renderer",
            view_type="table", component="Other"), object())
    assert not host.has("onto.test.renderer.duplicate")


def test_platform_meta_exposes_plugins_and_lock(platform, workspace_id):
    meta = smart_ui_meta(platform, workspace_id, "admin")
    assert meta["plugins"]
    assert any(item["id"] == "onto.smartui.view.table"
               for item in meta["plugins"])
    assert any(item["id"] == "onto.smartui.generator.llm"
               for item in meta["pluginLock"])
    assert all("enabled" in item for item in meta["plugins"])


def test_builtin_manifest_ids_are_unique_and_valid():
    ids = [manifest.id for manifest in BUILTIN_MANIFESTS]
    assert len(ids) == len(set(ids))
    assert all(manifest.builtin for manifest in BUILTIN_MANIFESTS)


def _wait(platform, workspace_id: str, generation_id: str,
          timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = get_generation(platform, workspace_id, "admin", generation_id)
        if row.get("status") in {"succeeded", "failed", "cancelled"}:
            return row
        time.sleep(0.02)
    raise AssertionError("generation did not settle")


def test_deterministic_generation_uses_the_generator_plugin_and_locks_it(
        platform, workspace_id):
    started = start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"],
        mode="deterministic")
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded"
    assert row["trace"]["generatorPlugin"] == (
        "onto.smartui.generator.deterministic")
    assert row["trace"]["pluginLock"] == platform.smartui.lock_dicts()
    assert row["pluginLock"] == platform.smartui.lock_dicts()
    draft = accept_generation(platform, workspace_id, "admin", row["id"])
    assert draft["plugin_lock"] == platform.smartui.lock_dicts()


def test_prompt_pack_is_a_plugin_contribution(platform):
    pack = platform.smartui.implementation("onto.smartui.prompt.default")
    system, user = pack.render(PromptRequest(pack={}, question="查看车辆"))
    assert "Blueprint" in system
    assert "chartType" in user
    assert "output_schema" in user


def test_table_and_chart_view_plugins_are_registered_and_used(platform):
    table = platform.smartui.implementation("onto.smartui.view.table")
    chart = platform.smartui.implementation("onto.smartui.view.chart")
    assert table.view_type == "table"
    assert table.component == "DataTable"
    assert chart.view_type == "chart"
    assert chart.component == "StatisticChart"
    obj = SimpleNamespace(properties=[
        SimpleNamespace(name="status"), SimpleNamespace(name="plate")])
    pruned: list[dict] = []
    options = table.adjudicate_options(
        {"chartType": "table", "columns": ["status", "unknown"]},
        obj, "statistics", pruned)
    assert options["columns"] == ["status"]
    table.adjudicate_options(
        {"chartType": "table", "columns": ["unknown"]},
        obj, "statistics", pruned)
    assert pruned
    assert table.options_schema()["properties"]["limit"]["maximum"] == 200

    called: list[str] = []
    original = chart.adjudicate_options

    def spy(*args, **kwargs):
        called.append(chart.id)
        return original(*args, **kwargs)

    chart.adjudicate_options = spy  # type: ignore[method-assign]
    try:
        _normalize_view_options(
            {"chartType": "bar"}, obj, "statistics", [], platform=platform)
    finally:
        chart.adjudicate_options = original  # type: ignore[method-assign]
    assert called == ["onto.smartui.view.chart"]


def test_adjudicator_chain_orders_sanitizer_before_schema(platform):
    records = platform.smartui.find("adjudicator")
    assert [record.manifest.id for record in records] == [
        "onto.smartui.adjudicator.sanitizer",
        "onto.smartui.adjudicator.default",
    ]
    context = AdjudicationContext(
        pack={}, question="", ontology=None, ontology_version="v1",
    )
    proposal = {"views": [{"perspective": "identity", "x": 10,
                           "style": {"color": "red"}, "title": "ok"}]}
    SanitizerAdjudicator().adjudicate(proposal, context)
    view = proposal["views"][0]
    assert "x" not in view and "style" not in view
    assert view["title"] == "ok"
    assert len(context.pruned) == 2


def test_declarative_workspace_packs_are_registered_and_validated(platform,
                                                                  workspace_id):
    assert {pack.id for pack in BUILTIN_PACKS} == {
        "onto.pack.traffic", "onto.pack.finance-lease",
        "onto.pack.digital-twin"}
    assert platform.smartui.pack("onto.pack.traffic").name == "高速运营"
    twin = platform.smartui.pack("onto.pack.digital-twin")
    assert twin.perspectives == ("identity", "location", "state", "rule")
    assert any(preset["options"].get("display") == "gis"
               for preset in twin.view_presets)
    assert len(platform.smartui.pack_summaries()) == 3
    meta = smart_ui_meta(platform, workspace_id, "admin")
    assert {item["id"] for item in meta["packs"]} == {
        "onto.pack.traffic", "onto.pack.finance-lease",
        "onto.pack.digital-twin"}
    # A pack is data only: unknown perspectives / non-declarative keys fail.
    with pytest.raises(ValidationError):
        WorkspacePack(id="onto.pack.bad", name="bad",
                      perspectives=("not_a_perspective",))
    with pytest.raises(ValidationError):
        WorkspacePack(id="onto.pack.bad", name="bad",
                      view_presets=({"perspective": "statistics",
                                     "options": {"script": "x"}},))
    loaded = load_pack({
        "id": "onto.pack.custom", "name": "自定义",
        "perspectives": ["identity"],
        "viewPresets": [{"perspective": "identity", "options": {"limit": 5}}],
    })
    assert loaded.id == "onto.pack.custom"
    assert loaded.view_presets[0]["options"]["limit"] == 5

