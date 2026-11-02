"""Smart-UI Studio: LLM proposal -> adjudication -> compile -> draft/publish."""

from __future__ import annotations

import copy
import json
import time

import pytest

from onto_platform.errors import PlatformError, ValidationError
from onto_platform.interfaces.smart_ui import _table_match, smart_ui_focus_options
from onto_platform.interfaces.smart_ui_studio import (
    accept_generation,
    application_ui_view,
    cancel_generation,
    delete_draft,
    delete_generation,
    generation_view,
    get_generation,
    list_drafts,
    list_generations,
    publish_generation,
    refine_generation,
    start_generation,
    update_draft,
)
from onto_platform.ports.llm import LLMResponse
from tests.conftest import http_delete, http_get, http_post, http_put


class FakeLLM:
    model = "fake-blueprint-model"

    def __init__(self, proposal: dict | None = None, *, raises: bool = False,
                 delay: float = 0.0, usage: dict | None = None,
                 raw: str | None = None) -> None:
        self.proposal = proposal
        self.raises = raises
        self.delay = delay
        self.usage = usage
        self.raw = raw
        self.calls = 0
        self.messages: list[dict] = []

    def complete(self, messages):
        self.calls += 1
        self.messages = list(messages)
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise ConnectionError("model endpoint is down")
        content = self.raw if self.raw is not None else json.dumps(
            self.proposal, ensure_ascii=False)
        return LLMResponse(content, usage=self.usage)


PROPOSAL = {
    "name": "VehicleSmartView",
    "displayName": "车辆智能视图",
    "description": "查看车辆画像与统计",
    "context": {
        "object": "Vehicle",
        "goal": "profile",
        "focus": {"key": "plate", "value": "鲁A12345"},
    },
    "views": [
        {"perspective": "identity"},
        {"perspective": "statistics"},
        {"perspective": "timeline"},  # Vehicle has no temporal property -> pruned
    ],
    "actions": ["recharge", "delete_vehicle"],  # unknown action -> pruned
    "confidence": 0.91,
}


def _wait(platform, wid: str, gid: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = get_generation(platform, wid, "admin", gid)
        if row["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return row
        time.sleep(0.02)
    raise AssertionError("generation did not finish in time")


def test_deterministic_generation_builds_a_ready_view(platform, workspace_id):
    started = start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"],
        mode="deterministic")
    assert started["status"] in {"queued", "running", "succeeded"}
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded"
    assert row["mode"] == "deterministic"
    assert row["blueprint"]["context"]["object"] == "Vehicle"
    assert row["view_count"] >= 1
    assert row["trace"]["programHash"]
    assert [step["status"] for step in row["steps"]][-1] == "ok"

    view = generation_view(platform, workspace_id, "admin", started["id"])
    assert view["compiled"]["program"]
    assert view["bundle"]["workspace"]["id"] == workspace_id


def test_llm_proposal_is_adjudicated_and_compiled(platform, workspace_id):
    fake = FakeLLM(PROPOSAL)
    platform.configure_llm(fake)
    started = start_generation(
        platform, workspace_id, "admin", "给鲁A12345 做一个车辆画像", ["owner"])
    row = _wait(platform, workspace_id, started["id"])

    assert row["status"] == "succeeded", row["error"]
    assert row["mode"] == "llm"
    assert fake.calls == 1
    assert row["blueprint"]["context"]["focus"] == {
        "key": "plate", "value": "鲁A12345"}
    assert [view["perspective"] for view in row["blueprint"]["views"]] == [
        "identity", "statistics"]
    assert row["blueprint"]["actions"] == ["recharge"]
    pruned = {(item["kind"], item["key"]) for item in row["validation"]["pruned"]}
    assert ("view", "timeline") in pruned
    assert ("action", "delete_vehicle") in pruned
    assert row["trace"]["model"] == "fake-blueprint-model"
    assert row["trace"]["promptHash"]
    assert row["trace"]["programHash"]
    assert row["metrics"]["totalMs"] >= 0
    assert all(step["durationMs"] is not None for step in row["steps"])

    # The blueprint focus must actually scope the rendered bundle, not just the
    # audit intent (the old compile path kept the platform default focus).
    view = generation_view(platform, workspace_id, "admin", started["id"])
    assert view["bundle"]["identity"]["plate"] == "鲁A12345"


def test_refine_carries_parent_lineage_and_diff(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    parent = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆总览", ["owner"])["id"])
    changed = copy.deepcopy(PROPOSAL)
    changed["displayName"] = "车辆值班卡"
    changed["views"] = [dict(view) for view in PROPOSAL["views"]]
    changed["views"][0]["title"] = "值班车辆"
    platform.configure_llm(FakeLLM(changed))
    child = _wait(platform, workspace_id, refine_generation(
        platform, workspace_id, "admin", parent["id"], "把状态再突出一点")["id"])
    detail = get_generation(platform, workspace_id, "admin", child["id"])
    assert [item["id"] for item in detail["lineage"]] == [child["id"], parent["id"]]
    assert detail["diff"]["changed"] is True
    assert any(change["kind"] == "view" for change in detail["diff"]["changes"])


def test_draft_can_be_renamed_and_deleted(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆总览", ["owner"])["id"])
    draft = accept_generation(platform, workspace_id, "admin", row["id"])
    renamed = update_draft(platform, workspace_id, "admin", draft["id"], "值班车辆")
    assert renamed["name"] == "值班车辆"
    assert delete_draft(platform, workspace_id, "admin", draft["id"])["deleted"]
    stored = platform.ctx.repo.get("ui_generation", workspace_id, row["id"])
    assert stored["draft_id"] is None


def test_delete_generation_cleans_unpublished_draft(platform, workspace_id):
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"],
        mode="deterministic")["id"])
    draft = accept_generation(platform, workspace_id, "admin", row["id"])
    result = delete_generation(platform, workspace_id, "admin", row["id"])
    assert result["deleted"] == row["id"]
    assert all(item["id"] != draft["id"]
               for item in list_drafts(platform, workspace_id, "admin")["items"])
    with pytest.raises(PlatformError):
        get_generation(platform, workspace_id, "admin", row["id"])


def test_delete_generation_refuses_published_artifact(platform, workspace_id):
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"],
        mode="deterministic")["id"])
    published = publish_generation(platform, workspace_id, "admin", row["id"],
                                   name="车辆删除保护")
    with pytest.raises(ValidationError, match="published"):
        delete_generation(platform, workspace_id, "admin", row["id"])
    assert published["draft"]["status"] == "published"


def test_list_generations_searches_and_paginates(platform, workspace_id):
    first = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆概览 alpha", ["owner"],
        mode="deterministic")["id"])
    _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆概览 beta", ["owner"],
        mode="deterministic")["id"])
    page = list_generations(platform, workspace_id, "admin",
                            limit=1, offset=0, query="alpha")
    assert page["total"] == 1
    assert page["items"][0]["id"] == first["id"]
    page = list_generations(platform, workspace_id, "admin",
                            limit=1, offset=1, status="succeeded")
    assert page["total"] >= 2
    assert len(page["items"]) == 1
    deterministic = list_generations(platform, workspace_id, "admin",
                                     mode="deterministic")
    assert deterministic["total"] >= 2
    assert all(item["mode"] == "deterministic"
               for item in deterministic["items"])
    assert list_generations(platform, workspace_id, "admin",
                            mode="fallback")["total"] == 0


def test_history_retention_prunes_old_unpublished(platform, workspace_id,
                                                  monkeypatch):
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "历史保留", ["owner"],
        mode="deterministic")["id"])
    draft = accept_generation(platform, workspace_id, "admin", row["id"])
    stored = platform.ctx.repo.get("ui_generation", workspace_id, row["id"])
    stored["created_at"] = "2020-01-01T00:00:00+00:00"
    platform.ctx.repo.put("ui_generation", workspace_id, row["id"], stored)
    monkeypatch.setenv("ONTO_SMARTUI_HISTORY_RETENTION_DAYS", "30")
    listed = list_generations(platform, workspace_id, "admin")
    assert all(item["id"] != row["id"] for item in listed["items"])
    assert platform.ctx.repo.get("ui_generation", workspace_id,
                                 row["id"]) is None
    assert platform.ctx.repo.get("ui_draft", workspace_id, draft["id"]) is None


def test_prompt_exposes_v11_options_to_the_model(platform, workspace_id):
    fake = FakeLLM(copy.deepcopy(PROPOSAL))
    platform.configure_llm(fake)
    _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin",
        "只看异常站点，按站点分组，最近 7 天，用表格展示", ["owner"])["id"])
    prompt = "\n".join(str(getattr(item, "content", "") or "")
                       for item in fake.messages)
    for token in ("chartType", "groupBy", "metric", "filters", "limit",
                  "table", "最近 7 天"):
        assert token in prompt
    assert "output_schema" in prompt


def test_usage_and_cost_are_recorded(platform, workspace_id, monkeypatch):
    monkeypatch.setenv("ONTO_SMARTUI_INPUT_COST_PER_1K", "0.01")
    monkeypatch.setenv("ONTO_SMARTUI_OUTPUT_COST_PER_1K", "0.02")
    monkeypatch.setenv("ONTO_SMARTUI_COST_CURRENCY", "CNY")
    platform.configure_llm(FakeLLM(
        copy.deepcopy(PROPOSAL),
        usage={"prompt_tokens": 1000, "completion_tokens": 500,
               "total_tokens": 1500}))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"])["id"])
    assert row["usage"]["total_tokens"] == 1500
    assert row["cost"]["amount"] == pytest.approx(0.02)
    assert row["cost"]["currency"] == "CNY"
    assert isinstance(row["durationMs"], int) and row["durationMs"] >= 0


def test_table_filter_semantics_support_bool_and_relative_time():
    assert _table_match("true", "eq", True)
    assert not _table_match("false", "eq", True)
    assert _table_match("True", "ne", False)
    assert _table_match("2026-11-01T10:00:00+00:00", "gte", "-7d")
    assert not _table_match("2020-01-01T10:00:00+00:00", "gte", "-7d")
    assert _table_match("3", "in", [3, 4])
    assert _table_match("济南西", "contains", "济南")
    assert _table_match("12.5", "gte", 10)


def test_table_options_produce_data_table(platform, workspace_id):
    proposal = copy.deepcopy(PROPOSAL)
    statistics = next(view for view in proposal["views"]
                      if view["perspective"] == "statistics")
    statistics["options"] = {
        "chartType": "table",
        "groupBy": "plate",
        "metric": {"field": "plate", "agg": "count"},
        "limit": 5,
        "sort": {"field": "value", "direction": "desc"},
        "filters": [{"field": "plate", "op": "contains", "value": "鲁"}],
    }
    platform.configure_llm(FakeLLM(proposal))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "用表格看车辆", ["owner"])["id"])
    assert row["status"] == "succeeded", row["error"]
    assert row["mode"] == "llm", (row["mode"], row["validation"])
    built = next(view for view in row["blueprint"]["views"]
                 if view["perspective"] == "statistics")
    assert built["options"]["chartType"] == "table"
    view = generation_view(platform, workspace_id, "admin", row["id"])
    assert "DataTable(" in view["compiled"]["program"]
    table = view["bundle"]["viewPayloads"]["view_statistics"]["rows"][0]["table"]
    assert table["columns"] == ["plate", "__value"]
    assert table["columnLabels"]["__value"] == "记录数"
    assert table["total"] >= 1
    assert table["rows"]
    assert all(row["plate"].startswith("鲁") for row in table["rows"])


def test_unknown_table_options_are_pruned(platform, workspace_id):
    proposal = copy.deepcopy(PROPOSAL)
    statistics = next(view for view in proposal["views"]
                      if view["perspective"] == "statistics")
    statistics["options"] = {
        "chartType": "table",
        "columns": ["plate", "not_a_property"],
        "sort": {"field": "not_a_property"},
        "filters": [{"field": "not_a_property", "op": "eq", "value": 1}],
        "limit": 999,
    }
    platform.configure_llm(FakeLLM(proposal))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "表格", ["owner"])["id"])
    assert row["mode"] == "llm", row["validation"]
    options = next(view for view in row["blueprint"]["views"]
                   if view["perspective"] == "statistics")["options"]
    assert options["columns"] == ["plate"]
    assert "sort" not in options and "filters" not in options
    pruned = {(item["kind"], item["key"]) for item in row["validation"]["pruned"]}
    assert ("option", "sort") in pruned
    assert ("option", "limit") in pruned


def test_explicit_focus_selection_scopes_generation(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    options = smart_ui_focus_options(
        platform, workspace_id, "admin", object_type="Vehicle")
    assert options["total"] >= 2
    selected = next(item for item in options["items"]
                    if item["value"] == "鲁B67890")
    started = start_generation(
        platform, workspace_id, "admin", "只查看这辆车", ["owner"],
        focus={"objectType": selected["objectType"], "key": selected["key"],
               "value": selected["value"]})
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded", row["error"]
    assert row["blueprint"]["context"]["focus"] == {
        "key": "plate", "value": "鲁B67890"}
    view = generation_view(platform, workspace_id, "admin", started["id"])
    assert view["bundle"]["identity"]["plate"] == "鲁B67890"


def test_unreachable_model_falls_back_to_deterministic_planner(platform, workspace_id):
    platform.configure_llm(FakeLLM(raises=True))
    started = start_generation(
        platform, workspace_id, "admin", "查看车辆情况", ["owner"])
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded"
    assert row["mode"] == "fallback"
    assert "模型不可用" in row["validation"]["fallbackReason"]
    assert row["validation"]["ok"] is True
    assert row["blueprint"]["context"]["object"] == "Vehicle"


def test_malformed_json_keeps_raw_preview_for_diagnosis(platform, workspace_id):
    platform.configure_llm(FakeLLM(raw="<html>proxy error</html>"))
    started = start_generation(
        platform, workspace_id, "admin", "生成车辆视图", ["owner"], mode="llm")
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "failed"
    assert "JSON" in row["error"]
    assert "<html>proxy error</html>" in row["trace"]["rawPreview"]


def test_invalid_proposal_auto_mode_uses_deterministic_safety_net(
        platform, workspace_id):
    platform.configure_llm(FakeLLM({"name": "Broken", "views": []}))
    started = start_generation(
        platform, workspace_id, "admin", "生成车辆视图", ["owner"])
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded"
    assert row["mode"] == "fallback"
    assert "模型或编译失败" in row["validation"]["fallbackReason"]
    assert generation_view(platform, workspace_id, "admin", started["id"])


def test_invalid_proposal_explicit_llm_mode_fails(platform, workspace_id):
    platform.configure_llm(FakeLLM({"name": "Broken", "views": []}))
    started = start_generation(
        platform, workspace_id, "admin", "生成车辆视图", ["owner"], mode="llm")
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "failed"
    assert row["error"]
    assert row["validation"]["ok"] is False
    with pytest.raises(PlatformError):
        generation_view(platform, workspace_id, "admin", started["id"])


def test_cancel_generation_is_terminal_and_never_overwritten(
        platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL, delay=0.3))
    started = start_generation(
        platform, workspace_id, "admin", "车辆画像", ["owner"])
    time.sleep(0.05)
    cancelled = cancel_generation(platform, workspace_id, "admin", started["id"])
    assert cancelled["status"] == "cancelled"
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "cancelled"
    with pytest.raises(PlatformError):
        generation_view(platform, workspace_id, "admin", started["id"])
    # Terminal statuses are idempotent, not an error.
    again = cancel_generation(platform, workspace_id, "admin", started["id"])
    assert again["status"] == "cancelled"


def test_refine_increments_version_and_keeps_parent(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    first = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆画像", ["owner"])["id"])
    second = refine_generation(
        platform, workspace_id, "admin", first["id"], "只看异常车辆")
    row = _wait(platform, workspace_id, second["id"])
    assert row["status"] == "succeeded"
    assert row["version"] == 2
    assert row["parent_id"] == first["id"]
    assert row["instruction"] == "只看异常车辆"
    listed = list_generations(platform, workspace_id, "admin")
    assert listed["total"] == 2


def test_accept_and_publish_attach_artifact_to_application(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆画像", ["owner"])["id"])
    draft = accept_generation(platform, workspace_id, "admin", row["id"])
    assert draft["status"] == "draft"
    assert draft["program_hash"] == row["trace"]["programHash"]
    assert list_drafts(platform, workspace_id, "admin")["total"] == 1

    published = publish_generation(platform, workspace_id, "admin", row["id"])
    assert published["draft"]["status"] == "published"
    assert published["draft"]["application_id"]
    artifact = published["application"]["ui_artifact"]
    assert artifact["generation_id"] == row["id"]
    assert artifact["version"] == draft["version"]
    assert artifact["blueprint"]["context"]["object"] == "Vehicle"
    assert artifact["program"]
    assert artifact["ontology_version"]

    # The published application is a serving surface even without hand-built
    # views/queries, and its artifact can render directly from the app.
    assert published["application"]["views"] == {}
    app_view = application_ui_view(
        platform, workspace_id, "admin", published["draft"]["application_id"])
    assert app_view["compiled"]["program"]
    assert app_view["application"]["id"] == published["draft"]["application_id"]
    assert app_view["artifact"]["version"] == draft["version"]
    app_published = platform.applications.publish(
        workspace_id, published["draft"]["application_id"], "admin")
    assert app_published["lifecycle"] == "published"

    # The same draft cannot be published twice.
    with pytest.raises(PlatformError):
        publish_generation(platform, workspace_id, "admin", row["id"])


def test_publish_can_target_an_existing_draft_application(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆画像", ["owner"])["id"])
    app = platform.applications.create(workspace_id, "admin", "已有草稿应用")
    published = publish_generation(
        platform, workspace_id, "admin", row["id"], application_id=app["id"])
    assert published["application"]["id"] == app["id"]
    assert published["application"]["ui_artifact"]["generation_id"] == row["id"]
    assert published["application"]["lifecycle"] == "draft"
    app_published = platform.applications.publish(
        workspace_id, app["id"], "admin")
    assert app_published["lifecycle"] == "published"


def test_stale_running_generation_is_recovered_on_read(platform, workspace_id):
    from onto_platform.interfaces.smart_ui_studio import GENERATION_KIND

    row = {
        "id": "gen_stale", "workspace_id": workspace_id, "status": "running",
        "mode": "llm", "question": "旧任务", "roles": ["owner"],
        "created_at": "2020-01-01T00:00:00+00:00",
        "updated_at": "2020-01-01T00:00:00+00:00",
        "steps": [{"key": "context", "label": "上下文", "status": "running",
                   "detail": ""}],
    }
    platform.ctx.repo.put(GENERATION_KIND, workspace_id, "gen_stale", row)
    listed = list_generations(platform, workspace_id, "admin")
    assert any(item["id"] == "gen_stale" and item["status"] == "interrupted"
               for item in listed["items"])
    recovered = get_generation(platform, workspace_id, "admin", "gen_stale")
    assert recovered["status"] == "interrupted"
    assert "重试" in recovered["error"]


def test_publish_into_published_application_is_refused(platform, workspace_id):
    platform.configure_llm(FakeLLM(PROPOSAL))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "车辆画像", ["owner"])["id"])
    app = platform.applications.create(workspace_id, "admin", "已发布应用")
    platform.applications.add_view(
        workspace_id, app["id"], "admin", "vehicles", title="车辆",
        kind="table", object_type="Vehicle", properties=["plate", "balance"])
    platform.applications.publish(workspace_id, app["id"], "admin")
    with pytest.raises(PlatformError):
        publish_generation(
            platform, workspace_id, "admin", row["id"],
            application_id=app["id"])


def test_studio_http_contract(http_server):
    base, wid = http_server
    status, created = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations",
        {"question": "查看车辆情况", "roles": ["owner"], "mode": "deterministic"})
    assert status == 202
    gid = created["id"]
    for _ in range(200):
        status, row = http_get(
            base, f"/api/workspaces/{wid}/smart-ui/generations/{gid}")
        assert status == 200
        if row["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert row["status"] == "succeeded"
    status, view = http_get(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{gid}/view")
    assert status == 200
    assert view["compiled"]["program"]
    status, listed = http_get(
        base, f"/api/workspaces/{wid}/smart-ui/generations?limit=5")
    assert status == 200
    assert listed["total"] >= 1
    status, searched = http_get(
        base,
        f"/api/workspaces/{wid}/smart-ui/generations?q={gid[:6]}"
        "&status=succeeded&object=Vehicle&limit=5&offset=0")
    assert status == 200
    assert searched["items"] and searched["items"][0]["id"] == gid
    status, draft = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{gid}/accept", {})
    assert status == 200
    assert draft["status"] == "draft"
    status, entities = http_get(
        base, f"/api/workspaces/{wid}/smart-ui/entities?object=Vehicle&limit=5")
    assert status == 200
    assert entities["items"] and entities["items"][0]["key"]
    status, renamed = http_put(
        base, f"/api/workspaces/{wid}/smart-ui/drafts/{draft['id']}",
        {"name": "车辆运营视图-改名"})
    assert status == 200
    assert renamed["name"] == "车辆运营视图-改名"
    status, published = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{gid}/publish",
        {"name": "车辆运营视图"})
    assert status == 200
    assert published["application"]["ui_artifact"]["generation_id"] == gid
    status, meta = http_get(base, f"/api/workspaces/{wid}/smart-ui/meta")
    assert status == 200
    assert "model" in meta and "will_use_model" in meta["model"]
    status, app_view = http_get(
        base,
        f"/api/workspaces/{wid}/applications/"
        f"{published['draft']['application_id']}/ui-artifact/view")
    assert status == 200
    assert app_view["compiled"]["program"]
    assert app_view["artifact"]["generation_id"] == gid
    status, second = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations",
        {"question": "查看车辆", "roles": ["owner"], "mode": "deterministic"})
    assert status == 202
    for _ in range(200):
        status, extra = http_get(
            base, f"/api/workspaces/{wid}/smart-ui/generations/{second['id']}")
        if extra["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    status, deleted = http_delete(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{second['id']}")
    assert status == 200 and deleted["deleted"] == second["id"]
    status, missing = http_get(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{second['id']}")
    assert status == 404
    status, body = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations",
        {"question": "", "roles": ["owner"]})
    assert status == 400
    status, body = http_delete(
        base, f"/api/workspaces/{wid}/smart-ui/drafts/{draft['id']}")
    assert status == 400
    assert "published" in str(body).lower()


def test_dimension_object_is_resolved_to_a_legal_query_root(platform, workspace_id):
    platform.configure_llm(FakeLLM({
        "name": "StationOverview",
        "displayName": "收费站总览",
        "context": {
            "object": "TollStation",
            "goal": "overview",
            "focus": {"key": "name", "value": "济南西收费站"},
        },
        "views": [{"perspective": "identity"}, {"perspective": "state"}],
        "actions": ["recharge"],
    }))
    row = _wait(platform, workspace_id, start_generation(
        platform, workspace_id, "admin", "收费站总览", ["owner"])["id"])
    assert row["status"] == "succeeded", row["error"]
    # TollStation is a dimension in this ontology: the platform resolves it to
    # a factual root instead of emitting a blueprint the L3 validator rejects.
    assert row["blueprint"]["context"]["object"] == "Vehicle"
    pruned = {(item["kind"], item["key"]) for item in row["validation"]["pruned"]}
    assert ("object", "TollStation") in pruned
    assert row["validation"]["ok"] is True
    object_check = next(c for c in row["validation"]["checks"] if c["key"] == "object")
    assert "查询根" in object_check["detail"]


def test_generation_requires_editor(platform, workspace_id):
    platform.workspaces.add_member(workspace_id, "viewer1", "viewer", actor="admin")
    with pytest.raises(PlatformError):
        start_generation(
            platform, workspace_id, "viewer1", "查看车辆", ["viewer"])
