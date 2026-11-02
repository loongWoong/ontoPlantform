"""Where the configured model is actually called.

The console saves a *workspace-scoped* OpenAI-compatible endpoint.  Every
LLM call path must resolve that endpoint (not the process-wide default), and
the endpoint URL must be assembled from the user's base URL correctly.
"""

from __future__ import annotations

import json
import pytest

from onto_platform.infrastructure.llm.echo import EchoLLMProvider
from onto_platform.infrastructure.llm.openai_compat import (
    OpenAICompatProvider,
    chat_completions_url,
)
from onto_platform.ports.llm import LLMResponse
from tests.conftest import make_platform, seed_workspace


class ExplodingProvider:
    """The platform default: any call here means workspace config was ignored."""

    def complete(self, messages):  # pragma: no cover - assertion is the point
        raise AssertionError("platform default provider must not be called")


class RecordingProvider:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = content if isinstance(content, list) else [content]
        self.calls = 0
        self.model = "workspace-model"

    def complete(self, messages):
        index = min(self.calls, len(self.contents) - 1)
        self.calls += 1
        return LLMResponse(self.contents[index])


@pytest.mark.parametrize("base,expected", [
    # host only: users routinely paste just this into the settings field
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000/v1/chat/completions"),
    ("http://127.0.0.1:8000/", "http://127.0.0.1:8000/v1/chat/completions"),
    ("https://api.openai.com", "https://api.openai.com/v1/chat/completions"),
    # already a base URL
    ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
    ("https://api.openai.com/v1/", "https://api.openai.com/v1/chat/completions"),
    # custom gateway path must be preserved, not double-prefixed
    ("https://gateway.example/openai/v1",
     "https://gateway.example/openai/v1/chat/completions"),
    # a copied full endpoint should not become /chat/completions/chat/completions
    ("http://127.0.0.1:8000/v1/chat/completions",
     "http://127.0.0.1:8000/v1/chat/completions"),
])
def test_base_url_is_normalized_to_one_chat_completions_path(base, expected):
    assert chat_completions_url(base) == expected
    assert OpenAICompatProvider(base_url=base, model="m")._url == expected


def test_llm_for_uses_workspace_endpoint_and_key():
    platform = make_platform()
    wid = seed_workspace(platform)
    default = ExplodingProvider()
    platform.ctx.llm = default

    # no workspace config yet -> platform default
    assert platform.ctx.llm_for(wid) is default

    platform.llm_config.put(wid, "admin", {
        "provider": "openai",
        "base_url": "http://127.0.0.1:8000",  # host-only on purpose
        "model": "deepseek-v4-flash",
        "api_key": "sk-workspace-secret",
    })
    provider = platform.ctx.llm_for(wid)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider._url == "http://127.0.0.1:8000/v1/chat/completions"
    assert provider._model == "deepseek-v4-flash"
    assert provider.model == "deepseek-v4-flash"
    assert provider._api_key == "sk-workspace-secret"


def test_llm_for_uses_keyless_workspace_endpoint_instead_of_default():
    """Local gateways (llmrouter/Ollama/vLLM) often need no API key."""
    platform = make_platform()
    wid = seed_workspace(platform)
    platform.ctx.llm = ExplodingProvider()
    platform.llm_config.put(wid, "admin", {
        "provider": "openai",
        "base_url": "http://127.0.0.1:8000",
        "model": "local-model",
    })
    provider = platform.ctx.llm_for(wid)
    assert isinstance(provider, OpenAICompatProvider)
    assert provider._api_key == ""
    assert provider._model == "local-model"


def test_ontology_draft_uses_workspace_provider_not_platform_default():
    platform = make_platform()
    wid = seed_workspace(platform)
    platform.ctx.llm = ExplodingProvider()
    workspace = RecordingProvider(json.dumps({
        "objects": [{"name": "Vehicle", "properties": [
            {"name": "plate", "type": "string"}]}],
        "relations": [],
        "vocabulary": [],
    }))
    platform.ctx.llm_for = lambda workspace_id: workspace

    result = platform.ontologies.draft(wid, "admin", "车辆档案", apply=False)
    assert workspace.calls == 1
    assert [o["name"] for o in result["draft"]["objects"]] == ["Vehicle"]


def test_agent_capability_uses_workspace_provider_not_platform_default():
    platform = make_platform()
    wid = seed_workspace(platform)
    platform.ctx.llm = ExplodingProvider()
    workspace = RecordingProvider([
        '{"think": "看", "action": {"tool": "get", '
        '"args": {"ref": "Vehicle/V001"}}}',
        '{"think": "答", "answer": "V001 正常"}',
    ])
    platform.ctx.llm_for = lambda workspace_id: workspace

    capability = platform.capabilities.create(
        wid, "admin", key="explain", name="解释车辆",
        target_type="Vehicle", description="解释车辆状态",
        implementation={"kind": "agent", "goal": "解释 {target}"})
    platform.capabilities.publish(wid, capability["id"], "admin")

    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.explain", target="Vehicle/V001", args={})
    assert result["ok"] is True
    assert workspace.calls == 2
    assert result["output"]["answer"] == "V001 正常"


def test_workspace_endpoint_is_used_by_the_agent_loop():
    """The app/agent API resolves the workspace provider at call time."""
    platform = make_platform()
    wid = seed_workspace(platform)
    platform.ctx.llm = ExplodingProvider()
    platform.ctx.llm_for = lambda workspace_id: EchoLLMProvider([
        '{"think": "答", "answer": "配置已生效"}',
    ])
    run = platform.intelligence.agent(wid, "admin", "检查配置")
    assert run["answer"] == "配置已生效"
