"""Rule-driven Smart-UI: no-model generation + rule-pack governance."""

from __future__ import annotations

import time

import pytest

from onto_platform.application.smartui.rules import (
    DEFAULT_RULE_PACK_ID,
    archive_rule_pack,
    bind_rule_pack,
    clone_rule_pack,
    create_rule_pack,
    dry_run_rule_pack,
    get_rule_pack,
    list_rule_packs,
    publish_rule_pack,
    rollback_rule_pack,
    update_rule_pack,
    validate_rule_pack,
)
from onto_platform.errors import PlatformError
from onto_platform.interfaces.smart_ui_studio import (
    get_generation,
    start_generation,
)
from tests.conftest import http_get, http_post


def _wait(platform, wid: str, gid: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = get_generation(platform, wid, "admin", gid)
        if row["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return row
        time.sleep(0.02)
    raise AssertionError("generation did not finish in time")


def test_rule_generation_needs_no_model(platform, workspace_id):
    started = start_generation(
        platform, workspace_id, "admin", "查看车辆画像与状态", ["owner"],
        mode="rule")
    row = _wait(platform, workspace_id, started["id"])

    assert row["status"] == "succeeded", row["error"]
    assert row["mode"] == "rule"
    assert row["requested_mode"] == "rule"
    assert row["trace"]["fallback"] is False
    assert row["rule_pack_id"] == DEFAULT_RULE_PACK_ID
    assert row["rule_pack_hash"].startswith("sha256:")
    trace = row["rule_trace"]
    assert trace["pack"]["id"] == DEFAULT_RULE_PACK_ID
    assert trace["goal"]
    assert trace["candidates"]
    assert row["blueprint"]["views"]
    assert row["trace"]["generatorPlugin"] == "onto.smartui.generator.rule"
    # the same L4 compiler remains authoritative
    assert row["trace"]["programHash"].startswith("sha256:")


def test_rule_pack_crud_validate_dry_run_publish_bind(platform, workspace_id):
    listed = list_rule_packs(platform, workspace_id, "admin")
    assert listed["defaultRulePackId"] == DEFAULT_RULE_PACK_ID
    assert any(item["id"] == DEFAULT_RULE_PACK_ID for item in listed["items"])

    created = create_rule_pack(platform, workspace_id, "admin", {
        "name": "车辆规则包",
        "description": "test",
        "baseId": DEFAULT_RULE_PACK_ID,
    })
    assert created["status"] == "draft"
    assert created["ruleCount"] > 0
    assert created["hash"].startswith("sha256:")

    checked = validate_rule_pack(platform, workspace_id, "admin", {
        "id": "bad.pack",
        "version": "1.0.0",
        "name": "bad",
        "rules": [{
            "id": "bad.view",
            "priority": 100,
            "when": {"goal.in": ["not_a_goal"]},
            "then": {"addView": [{"perspective": "not_a_view"}]},
            "reason": "bad",
        }],
    })
    assert checked["ok"] is False
    codes = {issue["code"] for issue in checked["issues"]}
    assert "E_RULE_UNKNOWN_GOAL" in codes
    assert "E_RULE_UNKNOWN_PERSPECTIVE" in codes

    dry = dry_run_rule_pack(
        platform, workspace_id, "admin", created["id"],
        {"question": "查看车辆趋势", "roles": ["owner"]})
    assert dry["ok"] is True, dry["issues"]
    assert dry["ruleTrace"]["candidates"]

    published = publish_rule_pack(platform, workspace_id, "admin",
                                 created["id"], note="v1")
    assert published["status"] == "published"
    with pytest.raises(PlatformError):
        update_rule_pack(platform, workspace_id, "admin", created["id"],
                         {"name": "cannot edit published"})

    bound = bind_rule_pack(platform, workspace_id, "admin", created["id"])
    assert bound["rulePackId"] == created["id"]
    assert list_rule_packs(platform, workspace_id, "admin")["boundRulePackId"] == created["id"]

    # Requested id wins over the workspace binding.
    fetched = get_rule_pack(platform, workspace_id, "admin", DEFAULT_RULE_PACK_ID)
    assert fetched["id"] == DEFAULT_RULE_PACK_ID
    assert fetched["status"] == "builtin"

    copy = clone_rule_pack(platform, workspace_id, "admin", created["id"],
                          name="车辆规则包 v2")
    assert copy["id"] != created["id"]
    assert copy["status"] == "draft"

    publish_rule_pack(platform, workspace_id, "admin", copy["id"])
    assert get_rule_pack(platform, workspace_id, "admin",
                         created["id"])["status"] == "archived"
    rolled = rollback_rule_pack(platform, workspace_id, "admin", created["id"])
    assert rolled["status"] == "published"
    assert get_rule_pack(platform, workspace_id, "admin",
                         copy["id"])["status"] == "archived"

    archived = archive_rule_pack(platform, workspace_id, "admin", created["id"])
    assert archived["status"] == "archived"


def test_rule_pack_http_contract(http_server):
    base, wid = http_server
    status, listed = http_get(base, f"/api/workspaces/{wid}/smart-ui/rule-packs")
    assert status == 200
    assert listed["defaultRulePackId"] == DEFAULT_RULE_PACK_ID

    status, pack = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/rule-packs",
        {"name": "HTTP 规则包", "baseId": DEFAULT_RULE_PACK_ID})
    assert status == 201, pack
    assert pack["status"] == "draft"

    status, checked = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/rule-packs/validate",
        {"id": "bad", "version": "1.0.0", "rules": []})
    assert status == 200
    assert checked["ok"] is False

    status, dry = http_post(
        base,
        f"/api/workspaces/{wid}/smart-ui/rule-packs/{pack['id']}/dry-run",
        {"question": "查看车辆趋势", "roles": ["owner"]})
    assert status == 200
    assert dry["ok"] is True, dry["issues"]
    assert dry["ruleTrace"]["candidates"]

    status, started = http_post(
        base, f"/api/workspaces/{wid}/smart-ui/generations",
        {"question": "查看车辆画像", "roles": ["owner"], "mode": "rule",
         "rule_pack_id": pack["id"]})
    assert status == 202, started
    for _ in range(200):
        status, row = http_get(
            base,
            f"/api/workspaces/{wid}/smart-ui/generations/{started['id']}")
        if row["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert row["status"] == "succeeded", row.get("error")
    assert row["mode"] == "rule"
    assert row["rule_pack_id"] == pack["id"]
    assert row["ruleTrace"]["pack"]["id"] == pack["id"]

