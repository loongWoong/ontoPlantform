"""Regression: a capability must not invoke itself recursively.

Agent-kind capabilities are registered as kernel functions, so they show up
in the runtime index next to the real business functions.  A naive model can
therefore "implement" its own goal by invoking its own function again; each
level re-enters the executor (and, for agents, makes another LLM call) until
the call stack or the token budget is gone.  The platform must (a) hide the
capability's own function from the agent's index, (b) reject it as a tool,
and (c) reject any recursive capability invocation in the gateway.
"""

from __future__ import annotations

from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.infrastructure.llm.echo import EchoLLMProvider
from tests.conftest import seed_workspace


def _make_agent_capability(platform, wid, script):
    platform.configure_llm(EchoLLMProvider(script))
    capability = platform.capabilities.create(
        wid, "admin", key="explain", name="说明", target_type="Vehicle",
        description="", implementation={"kind": "agent", "goal": "说明 {target}"})
    platform.capabilities.publish(wid, capability["id"], "admin")
    return capability


def test_agent_runner_hides_its_own_function_from_the_index():
    from onto_platform.application.agent_runner import _hide_functions

    platform = build_platform(PlatformConfig(store="memory"))
    wid = seed_workspace(platform)
    _make_agent_capability(platform, wid, ['{"think": "ok", "answer": "ok"}'])

    described = platform.ontologies.describe_runtime(wid, "admin")
    names = [spec.get("name") for spec in described["functions"]]
    assert "Vehicle.explain" in names  # platform-level introspection keeps it

    agent_view = _hide_functions(described, frozenset({"Vehicle.explain"}))
    agent_names = [spec.get("name") for spec in agent_view["functions"]]
    assert "Vehicle.explain" not in agent_names
    assert "Vehicle.recharge" in agent_names  # real business functions stay


def test_agent_capability_refuses_to_invoke_itself():
    platform = build_platform(PlatformConfig(store="memory"))
    wid = seed_workspace(platform)
    _make_agent_capability(platform, wid, [
        '{"think": "自己动手", "action": {"tool": "invoke_capability", '
        '"args": {"function": "Vehicle.explain", "target": "Vehicle/V001"}}}',
        '{"think": "换观察工具", "action": {"tool": "get", "args": {"ref": "Vehicle/V001"}}}',
        '{"think": "结论", "answer": "V001 余额 100"}',
    ])

    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.explain", target="Vehicle/V001")

    assert result["ok"] is True
    assert result["output"]["steps"] == 3  # bounded: no recursive re-entry
    assert result["output"]["answer"] == "V001 余额 100"


def test_gateway_rejects_recursive_capability_invocation():
    platform = build_platform(PlatformConfig(store="memory"))
    wid = seed_workspace(platform)
    capability = platform.capabilities.create(
        wid, "admin", key="loop", name="自调用", target_type="TollStation",
        description="", implementation={"kind": "workflow", "steps": [
            {"kind": "invoke", "function": "TollStation.loop",
             "target": "{{target}}"},
        ]})
    platform.capabilities.publish(wid, capability["id"], "admin")

    result = platform.capabilities.invoke(
        wid, "admin", "TollStation.loop", target="TollStation/ST001")

    assert result["ok"] is False
    assert "recursive capability invocation rejected" in (result["error"] or "")
