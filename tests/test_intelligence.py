"""Intelligence: AskOntology (deterministic) + Agent loop (scripted LLM)。"""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError
from onto_platform.infrastructure.llm.echo import EchoLLMProvider


def test_ask_resolves_vocabulary_and_values(platform, workspace_id):
    answer = platform.intelligence.ask(workspace_id, "admin", "济南西收费站现在为什么拥堵？")
    assert any(h["term"] == "收费站" for h in answer["concepts"])
    assert any(h["term"] == "拥堵" for h in answer["concepts"])
    # the mentioned station is located by its name value
    assert any(o["id"] == "ST001" for o in answer["objects"])
    assert "济南西收费站" in answer["answer"]
    # one-hop relation expansion brings the passing vehicles
    assert any(r["object"]["type"] == "Vehicle" for r in answer["related"])


def test_ask_without_match(platform, workspace_id):
    answer = platform.intelligence.ask(workspace_id, "admin", "今天天气怎么样？")
    assert "未找到匹配对象" in answer["answer"]


def test_agent_full_loop_with_scripted_llm(platform, workspace_id):
    script = [
        '{"think": "先找拥堵收费站", "action": {"tool": "find", "args": '
        '{"object_type": "TollStation", "filter": {"status": "congested"}}}}',
        '{"think": "查看通过它的车辆", "action": {"tool": "related", "args": '
        '{"ref": "TollStation/ST001", "relation": "passes", "direction": "in"}}}',
        '{"think": "信息足够", "answer": "济南西收费站拥堵，有两辆车通过。"}',
    ]
    run = platform.intelligence.agent(
        workspace_id, "admin", "济南西收费站为什么拥堵？",
        provider=EchoLLMProvider(script))
    assert run["ok"] is True
    assert len(run["steps"]) == 3
    assert run["answer"] == "济南西收费站拥堵，有两辆车通过。"
    # observations actually flowed back into the loop
    assert "ST001" in run["steps"][0]["observation"]
    assert "Vehicle/V001" in run["steps"][1]["observation"]
    # the run and every step are persisted, not just returned to the caller
    stored = platform.ctx.repo.get("agent_run", workspace_id, run["run_id"])
    assert stored["ok"] is True
    assert stored["duration_ms"] >= 0
    assert stored["usage"] is None  # the echo provider reports no tokens
    steps = [s for s in platform.ctx.repo.list("agent_step", workspace_id)
             if s.get("run_id") == run["run_id"]]
    assert len(steps) == len(run["steps"]) == 3


def test_agent_can_invoke_capabilities(platform, workspace_id):
    script = [
        '{"think": "充值", "action": {"tool": "invoke_capability", "args": '
        '{"function": "Vehicle.recharge", "target": "Vehicle/V002", "args": {"amount": 80}}}}',
        '{"think": "验证", "action": {"tool": "get", "args": {"ref": "Vehicle/V002"}}}',
        '{"think": "完成", "answer": "已充值，余额 100。"}',
    ]
    run = platform.intelligence.agent(
        workspace_id, "admin", "给鲁B67890充值80元",
        provider=EchoLLMProvider(script))
    assert run["ok"] is True
    vehicle = platform.ctx.gateways.for_workspace(workspace_id, {}).get_object(
        "Vehicle/V002")
    assert vehicle["properties"]["balance"] == 100


def test_agent_handles_bad_json_and_tool_errors(platform, workspace_id):
    script = [
        '这不是JSON',
        '{"think": "调用不存在的能力", "action": {"tool": "invoke_capability", '
        '"args": {"function": "Ghost.no"}}}',
        '{"think": "放弃", "answer": "无法完成"}',
    ]
    run = platform.intelligence.agent(
        workspace_id, "admin", "做点什么",
        provider=EchoLLMProvider(script))
    assert run["ok"] is True
    # the non-JSON turn produced no step; the failed invoke came back as a
    # structured error inside the observation instead of raising
    assert "FunctionNotFound" in run["steps"][0]["observation"]
    assert run["steps"][1]["action"]["tool"] == "finish"


def test_agent_max_steps_guard(platform, workspace_id):
    """A tool-loop that never answers is cut at max_steps and reported as an
    explicit unverified failure, not a manufactured answer."""
    script = [
        '{"think": "继续", "action": {"tool": "describe_ontology", "args": {}}}',
        '{"think": "继续", "action": {"tool": "describe_ontology", "args": {}}}',
        '{"think": "收尾", "answer": "观察够了，给出结论"}',
    ]
    run = platform.intelligence.agent(
        workspace_id, "admin", "无限循环",
        provider=EchoLLMProvider(script), max_steps=2)
    assert run["ok"] is False
    assert run["termination"] == "max_steps"
    assert run["verified"] is False
    assert len(run["steps"]) == 2


def test_agent_requires_provider(platform, workspace_id):
    with pytest.raises(ValidationError):
        platform.intelligence.agent(workspace_id, "admin", "目标")
