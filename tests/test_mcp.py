"""MCP adapter tests (design.txt §36/§46): the platform exposed as MCP tools.

Skipped when the optional ``mcp`` package is not installed.
"""

from __future__ import annotations

import json

import pytest

mcp = pytest.importorskip("mcp.server.fastmcp")

from onto_platform.bootstrap import build_platform  # noqa: E402
from onto_platform.infrastructure.llm.echo import EchoLLMProvider  # noqa: E402
from onto_platform.interfaces.mcp import build_server  # noqa: E402
from tests.conftest import make_platform, seed_workspace  # noqa: E402


@pytest.fixture()
def server(platform, workspace_id):
    return build_server(platform=platform, workspace_id=workspace_id)


def _call(server, tool: str, arguments: dict) -> dict:
    import asyncio

    async def _run():
        contents, _meta = await server.call_tool(tool, arguments)
        return json.loads(contents[0].text)

    return asyncio.run(_run())


def test_mcp_exposes_workspace_tools(server):
    tools = asyncio_run(server.list_tools())
    names = {t.name for t in tools}
    assert {"workspace_describe", "workspace_find", "workspace_get",
            "workspace_related", "workspace_ask", "capability_invoke",
            "agent_run"} <= names


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_mcp_describe_and_invoke(platform, workspace_id, server):
    describe = _call(server, "workspace_describe", {})
    assert describe["workspace_id"] == workspace_id
    assert "TollStation" in describe["ontology"]["objects"]
    assert any(c["function"] == "Vehicle.recharge"
               for c in describe["capabilities"])

    result = _call(server, "capability_invoke", {
        "function": "Vehicle.recharge", "target": "Vehicle/V001",
        "args": {"amount": 50}})
    assert result["ok"] is True

    found = _call(server, "workspace_find",
                  {"object_type": "Vehicle", "filters": {"balance__gt": 100}})
    assert any(o["id"] == "V001" for o in found)


def test_mcp_ask_and_agent(platform, workspace_id, server):
    platform.configure_llm(EchoLLMProvider([
        '{"think": "答", "answer": "ST001 拥堵"}',
    ]))
    answer = _call(server, "workspace_ask", {"question": "哪些收费站拥堵"})
    assert answer["question"] == "哪些收费站拥堵"

    run = _call(server, "agent_run", {"goal": "检查拥堵"})
    assert run["ok"] is True
