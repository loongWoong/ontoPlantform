"""Smart-UI Studio: LLM proposal -> adjudication -> compile -> draft/publish."""

from __future__ import annotations

import json
import time

import pytest

from onto_platform.errors import PlatformError, ValidationError
from onto_platform.interfaces.smart_ui_studio import (
    accept_generation,
    generation_view,
    get_generation,
    list_drafts,
    list_generations,
    publish_generation,
    refine_generation,
    start_generation,
)
from onto_platform.ports.llm import LLMResponse
from tests.conftest import http_get, http_post


class FakeLLM:
    model = "fake-blueprint-model"

    def __init__(self, proposal: dict | None = None, *, raises: bool = False) -> None:
        self.proposal = proposal
        self.raises = raises
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        if self.raises:
            raise ConnectionError("model endpoint is down")
        return LLMResponse(json.dumps(self.proposal, ensure_ascii=False))


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
        if row["status"] in {"succeeded", "failed"}:
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
    assert row["mode"] == "fallback"
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


def test_invalid_proposal_fails_without_rendering(platform, workspace_id):
    platform.configure_llm(FakeLLM({"name": "Broken", "views": []}))
    started = start_generation(
        platform, workspace_id, "admin", "生成车辆视图", ["owner"])
    row = _wait(platform, workspace_id, started["id"])
    assert row["status"] == "failed"
    assert row["error"]
    assert row["validation"]["ok"] is False
    with pytest.raises(PlatformError):
        generation_view(platform, workspace_id, "admin", started["id"])


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
    assert published["application"]["ui_artifact"]["generation_id"] == row["id"]
    assert published["application"]["ui_artifact"]["version"] == draft["version"]

    # The same draft cannot be published twice.
    with pytest.raises(PlatformError):
        publish_generation(platform, workspace_id, "admin", row["id"])


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
    status, draft = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{gid}/accept", {})
    assert status == 200
    assert draft["status"] == "draft"
    status, published = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations/{gid}/publish",
        {"name": "车辆运营视图"})
    assert status == 200
    assert published["application"]["ui_artifact"]["generation_id"] == gid
    status, body = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations",
        {"question": "", "roles": ["owner"]})
    assert status == 400


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
