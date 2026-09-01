"""EchoLLMProvider: a scripted, deterministic provider.

Plays back prepared responses in order - used by tests and by the demo
agent so the full Agent loop runs without any API key.
"""

from __future__ import annotations


class EchoLLMProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages) -> object:
        from ...ports.llm import LLMResponse

        if self._responses:
            return LLMResponse(content=self._responses.pop(0))
        return LLMResponse(content='{"think": "script exhausted", "answer": "无法继续推演"}')
