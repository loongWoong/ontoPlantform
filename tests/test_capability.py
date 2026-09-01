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


# ----------------------------------------------------------------------
# workflow set_property: the bounded mutation primitive (design.txt §49)
# ----------------------------------------------------------------------


def test_workflow_set_property_mutates_state(platform, workspace_id):
    platform.capabilities.create(
        workspace_id, "admin", key="mark_busy", name="标记繁忙",
        target_type="TollStation", description="写回通行计数",
        implementation={"kind": "workflow", "steps": [
            {"kind": "related", "ref": "{{target}}", "relation": "passes",
             "direction": "in", "as": "vehicles"},
            {"kind": "count", "list": "{{steps.vehicles}}", "as": "n"},
            {"kind": "set_property", "ref": "{{target}}",
             "properties": {"status": "busy"}},
        ]})
    published = platform.capabilities.list(workspace_id, "admin")
    platform.capabilities.publish(workspace_id, published[-1]["id"], "admin")

    result = platform.capabilities.invoke(
        workspace_id, "admin", "TollStation.mark_busy",
        target="TollStation/ST001", args={})
    assert result["ok"] is True
    station = platform.ctx.gateways.for_workspace(workspace_id, {}).get_object(
        "TollStation/ST001")
    assert station["properties"]["status"] == "busy"
    # the mutation is recorded as a proper kernel change
    assert any(c["source"] == "invoke" for c in
               platform.ctx.gateways.for_workspace(workspace_id, {}).observe_changes())


def test_workflow_set_property_rejects_bad_ref(platform, workspace_id):
    platform.capabilities.create(
        workspace_id, "admin", key="bad_write", name="坏写",
        target_type="TollStation", description="",
        implementation={"kind": "workflow", "steps": [
            {"kind": "set_property", "ref": "not-a-ref",
             "properties": {"status": "free"}}]})
    published = platform.capabilities.list(workspace_id, "admin")
    platform.capabilities.publish(workspace_id, published[-1]["id"], "admin")
    result = platform.capabilities.invoke(
        workspace_id, "admin", "TollStation.bad_write",
        target="TollStation/ST001", args={})
    assert result["ok"] is False
    assert "set_property" in result["error"]


# ----------------------------------------------------------------------
# agent-implemented capability (design.txt §19: Agent / AI + Function)
# ----------------------------------------------------------------------


def test_agent_capability_end_to_end(platform, workspace_id):
    from onto_platform.infrastructure.llm.echo import EchoLLMProvider

    platform.configure_llm(EchoLLMProvider([
        '{"think": "查看站点", "action": {"tool": "get", "args": {"ref": "TollStation/ST001"}}}',
        '{"think": "结论", "answer": "ST001 正在拥堵"}',
    ]))
    capability = platform.capabilities.create(
        workspace_id, "admin", key="explain", name="拥堵解释",
        target_type="TollStation", description="解释站点状态",
        implementation={"kind": "agent", "goal": "解释 {target} 的运行状态"})
    platform.capabilities.publish(workspace_id, capability["id"], "admin")

    result = platform.capabilities.invoke(
        workspace_id, "admin", "TollStation.explain",
        target="TollStation/ST001", args={})
    assert result["ok"] is True
    assert result["output"]["answer"] == "ST001 正在拥堵"


def test_agent_capability_without_llm_is_a_clean_error(platform, workspace_id):
    capability = platform.capabilities.create(
        workspace_id, "admin", key="explain", name="拥堵解释",
        target_type="TollStation", description="",
        implementation={"kind": "agent", "goal": "解释 {target}"})
    platform.capabilities.publish(workspace_id, capability["id"], "admin")
    result = platform.capabilities.invoke(
        workspace_id, "admin", "TollStation.explain",
        target="TollStation/ST001", args={})
    assert result["ok"] is False
    assert "LLM provider" in result["error"]


def test_agent_capability_survives_process_restart(tmp_path):
    """New platform instance over the same db: gateway self-heals and
    re-attaches agent runners (definitions in repo, runner rebuilt)."""
    from onto_platform.bootstrap import PlatformConfig, build_platform
    from onto_platform.infrastructure.llm.echo import EchoLLMProvider
    from tests.conftest import seed_workspace

    db = str(tmp_path / "restart.db")
    script = [
        '{"think": "看", "action": {"tool": "get", "args": {"ref": "Vehicle/V001"}}}',
        '{"think": "答", "answer": "V001 余额 100"}',
    ]
    platform = build_platform(PlatformConfig(store="sqlite", db_path=db))
    wid = seed_workspace(platform)
    platform.configure_llm(EchoLLMProvider(script))
    capability = platform.capabilities.create(
        wid, "admin", key="explain", name="解释", target_type="Vehicle",
        description="", implementation={"kind": "agent", "goal": "解释 {target}"})
    platform.capabilities.publish(wid, capability["id"], "admin")

    # simulate a fresh process: new platform over the same database
    platform2 = build_platform(PlatformConfig(store="sqlite", db_path=db))
    platform2.configure_llm(EchoLLMProvider(script))
    result = platform2.capabilities.invoke(
        wid, "admin", "Vehicle.explain", target="Vehicle/V001", args={})
    assert result["ok"] is True
    assert result["output"]["answer"] == "V001 余额 100"
