"""Phase 1.7: workflow Saga (compensation, timeout, unknown handling)."""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError

from conftest import seed_workspace


def _publish_workflow(platform, wid, key, steps):
    capability = platform.capabilities.create(
        wid, "admin", key=key, name=key, target_type="Vehicle",
        implementation={"kind": "workflow", "steps": steps})
    platform.capabilities.publish(wid, capability["id"], "admin")
    return capability


def _balance(platform, wid):
    return platform.ctx.gateway_for(wid).get_object(
        "Vehicle/V001")["properties"]["balance"]


def test_failed_step_compensates_completed_steps(platform):
    wid = seed_workspace(platform)
    assert _balance(platform, wid) == 100

    _publish_workflow(platform, wid, "saga", [
        {"kind": "invoke", "function": "Vehicle.recharge",
         "target": "{{target}}", "args": {"amount": 5}, "as": "debit",
         "compensate": {
             "kind": "invoke", "function": "Vehicle.recharge",
             "target": "{{target}}", "args": {"amount": -5}}},
        # this step cannot succeed, so the debit above must be undone
        {"kind": "invoke", "function": "Vehicle.nope", "target": "{{target}}"},
    ])

    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.saga", target="Vehicle/V001",
        idempotency_key="saga-1")
    assert result["status"] == "failed"
    assert "compensated" in result["error"]
    assert _balance(platform, wid) == 100, "the completed step must be undone"

    record = platform.ctx.repo.get("execution", wid, "key_saga-1")
    assert record["status"] == "failed"


def test_compensation_failure_is_reported(platform):
    wid = seed_workspace(platform)
    _publish_workflow(platform, wid, "saga2", [
        {"kind": "invoke", "function": "Vehicle.recharge",
         "target": "{{target}}", "args": {"amount": 7}, "as": "debit",
         "compensate": {
             "kind": "invoke", "function": "Vehicle.absent",
             "target": "{{target}}"}},
        {"kind": "invoke", "function": "Vehicle.nope", "target": "{{target}}"},
    ])
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.saga2", target="Vehicle/V001")
    assert result["status"] == "failed"
    assert "COMPENSATION FAILURES" in result["error"]
    # the original debit is still there - and the caller is told so
    assert _balance(platform, wid) == 107


def test_workflow_steps_are_validated(platform):
    wid = seed_workspace(platform)
    with pytest.raises(ValidationError):
        platform.capabilities.create(
            wid, "admin", key="bad", name="bad", target_type="Vehicle",
            implementation={"kind": "workflow",
                            "steps": [{"kind": "teleport"}]})
