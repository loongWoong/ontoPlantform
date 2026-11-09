"""P2 twin boundary: rule-level what-if only (single object, no side effects)."""

from __future__ import annotations

import pytest

from onto_platform.errors import NotFoundError, ValidationError
from tests.conftest import http_post


def _rule(platform, wid):
    created = platform.rules.create(
        wid, "admin", "Low balance", "Vehicle",
        {"field": "balance", "op": "lt", "value": 25},
        message="balance below 25")
    return platform.rules.publish(wid, "admin", created["id"])


def test_simulate_reports_resolved_violation(platform, workspace_id):
    _rule(platform, workspace_id)

    result = platform.rules.simulate(
        workspace_id, "admin", "Vehicle/V002", {"balance": 30})

    assert result["before"]["passed"] is False
    assert result["after"]["passed"] is True
    assert result["delta"]["changed"] is True
    assert len(result["delta"]["resolved"]) == 1
    assert result["delta"]["introduced"] == []
    # no side effect: the stored object still has the original value
    assert platform.ctx.gateways.for_workspace(
        workspace_id, platform.ctx.default_runtime_config
    ).get_object("Vehicle/V002")["properties"]["balance"] == 20


def test_simulate_reports_unchanged_and_introduced(platform, workspace_id):
    _rule(platform, workspace_id)

    unchanged = platform.rules.simulate(
        workspace_id, "admin", "Vehicle/V001", {"balance": 100})
    assert unchanged["delta"]["changed"] is False

    introduced = platform.rules.simulate(
        workspace_id, "admin", "Vehicle/V001", {"balance": 10})
    assert introduced["before"]["passed"] is True
    assert introduced["after"]["passed"] is False
    assert len(introduced["delta"]["introduced"]) == 1


def test_simulate_validates_target_and_changes(platform, workspace_id):
    _rule(platform, workspace_id)
    with pytest.raises(NotFoundError):
        platform.rules.simulate(
            workspace_id, "admin", "Vehicle/NOPE", {"balance": 1})
    with pytest.raises(ValidationError, match="non-empty"):
        platform.rules.simulate(workspace_id, "admin", "Vehicle/V001", {})
    for changes in ({"id": "x"}, {"properties": {}}, {"address.city": "x"},
                    {"$type": "x"}, {"": "x"}):
        with pytest.raises(ValidationError):
            platform.rules.simulate(
                workspace_id, "admin", "Vehicle/V001", changes)


def test_simulate_is_audited(platform, workspace_id):
    _rule(platform, workspace_id)
    platform.rules.simulate(
        workspace_id, "admin", "Vehicle/V002", {"balance": 30})

    entries = [entry for entry in platform.ctx.audit.list(workspace_id, 500)
               if entry["action"] == "rule.simulate"]
    assert entries
    detail = entries[-1]["detail"]
    assert detail["resolved"] == 1
    assert detail["introduced"] == 0
    assert detail["passed_after"] is True


def test_simulate_http_route(http_server):
    base, wid = http_server
    status, rule = http_post(base, f"/api/workspaces/{wid}/rules", {
        "name": "Low balance", "object_type": "Vehicle",
        "condition": {"field": "balance", "op": "lt", "value": 25},
        "message": "balance below 25",
    })
    assert status == 200, rule
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/rules/{rule['id']}/publish", {})
    assert status == 200

    status, body = http_post(
        base, f"/api/workspaces/{wid}/rules/simulate",
        {"ref": "Vehicle/V002", "changes": {"balance": 30}})

    assert status == 200, body
    assert body["after"]["passed"] is True
    assert len(body["delta"]["resolved"]) == 1
