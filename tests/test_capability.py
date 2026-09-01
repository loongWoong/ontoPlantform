"""Capability: python 实现 + workflow 编排 (§17-§20)。"""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError
from onto_platform.infrastructure.execution.workflow_runner import WorkflowStepError
from onto_platform.infrastructure.execution.workflow_runner import (
    GatewayWorkflowRunner,
    _resolve,
)

# ----------------------------------------------------------------------
# python implementation
# ----------------------------------------------------------------------


def test_python_capability_end_to_end(platform, workspace_id):
    result = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.recharge",
        target="Vehicle/V001", args={"amount": 50})
    assert result["ok"] is True
    assert result["output"] == {"success": True, "balance": 150}
    assert result["changes"] == [{
        "kind": "set_property", "object": "Vehicle/V001",
        "property": "balance", "old": 100, "new": 150,
    }]


def test_invoke_error_paths(platform, workspace_id):
    missing = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.recharge", target="Vehicle/V999",
        args={"amount": 1})
    assert missing["ok"] is False and "ObjectNotFound" in missing["error"]

    bad_args = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": "many"})
    assert bad_args["ok"] is False and "InvalidArgument" in bad_args["error"]

    unknown = platform.capabilities.invoke(workspace_id, "admin", "Ghost.no")
    assert unknown["ok"] is False and "FunctionNotFound" in unknown["error"]


def test_function_failure_leaves_state_untouched(platform, workspace_id):
    platform.capabilities.create(
        workspace_id, "admin", key="explode", name="爆炸", target_type="Vehicle",
        description="always fails",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": """
def execute(ctx):
    ctx.set(ctx.object().ref, "balance", 0)
    ctx.fail("boom")
"""})
    platform.capabilities.publish(
        workspace_id,
        platform.capabilities.list(workspace_id, "admin")[-1]["id"], "admin")
    before = platform.ctx.gateways.for_workspace(workspace_id, {}).get_object(
        "Vehicle/V001")
    result = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.explode", target="Vehicle/V001",
        args={"amount": 1})
    assert result["ok"] is False
    after = platform.ctx.gateways.for_workspace(workspace_id, {}).get_object(
        "Vehicle/V001")
    assert after == before  # atomicity: failed invocation changes nothing


# ----------------------------------------------------------------------
# workflow implementation
# ----------------------------------------------------------------------


def make_workflow_platform(platform, workspace_id):
    platform.capabilities.create(
        workspace_id, "admin", key="congestion_report", name="拥堵分析",
        target_type="TollStation", description="统计通过车辆",
        inputs=[{"name": "threshold", "type": "integer"}],
        implementation={"kind": "workflow", "steps": [
            {"kind": "related", "ref": "{{target}}", "relation": "passes",
             "direction": "in", "as": "vehicles"},
            {"kind": "count", "list": "{{steps.vehicles}}", "as": "vehicle_count"},
            {"kind": "get", "ref": "{{target}}", "as": "station"},
            {"kind": "set", "name": "report_title", "value": "站点 {{target}} 分析",
             "as": "output"},
        ]})
    published = platform.capabilities.list(workspace_id, "admin")
    platform.capabilities.publish(workspace_id, published[-1]["id"], "admin")


def test_workflow_capability_end_to_end(platform, workspace_id):
    make_workflow_platform(platform, workspace_id)
    result = platform.capabilities.invoke(
        workspace_id, "admin", "TollStation.congestion_report",
        target="TollStation/ST001", args={"threshold": 2})
    assert result["ok"] is True
    # the step aliased "output" wins
    assert result["output"] == "站点 TollStation/ST001 分析"
    # intermediate results are not visible in output but the workflow ran
    assert result["changes"] == []  # observation workflow mutates nothing


def test_workflow_step_failure_becomes_error(platform, workspace_id):
    platform.capabilities.create(
        workspace_id, "admin", key="broken_flow", name="坏流程",
        target_type="TollStation", description="",
        implementation={"kind": "workflow", "steps": [
            {"kind": "invoke", "function": "Nope.nothing"},
        ]})
    published = platform.capabilities.list(workspace_id, "admin")
    platform.capabilities.publish(workspace_id, published[-1]["id"], "admin")
    result = platform.capabilities.invoke(
        workspace_id, "admin", "TollStation.broken_flow",
        target="TollStation/ST001", args={})
    assert result["ok"] is False
    assert "Nope.nothing" in result["error"]


def test_template_resolution():
    ctx = {"args": {"amount": 5}, "target": "Vehicle/V001",
           "steps": {"s1": [{"id": "V001"}, {"id": "V002"}]}, "vars": {}}
    assert _resolve("{{args.amount}}", ctx) == 5
    assert _resolve("{{target}}", ctx) == "Vehicle/V001"
    assert _resolve("{{steps.s1.1.id}}", ctx) == "V002"
    assert _resolve("站点 {{target}} !", ctx) == "站点 Vehicle/V001 !"
    assert _resolve({"x": "{{args.amount}}"}, ctx) == {"x": 5}
    with pytest.raises(WorkflowStepError):
        _resolve("{{steps.nope}}", ctx)
    with pytest.raises(WorkflowStepError):
        _resolve("{{evil.path}}", ctx)


def test_runner_direct(platform, workspace_id):
    gateway = platform.ctx.gateways.for_workspace(workspace_id, {})
    runner = GatewayWorkflowRunner(gateway)
    ctx = runner.run(
        [{"kind": "find", "object_type": "TollStation",
          "filter": {"status": "congested"}, "as": "output"}],
        {"args": {}, "target": None})
    assert [o["id"] for o in ctx["output"]] == ["ST001"]
