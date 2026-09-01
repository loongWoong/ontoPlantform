"""LLMProvider Port: understanding and generation, model-agnostic.

Domain never depends on OpenAI / Qwen / GLM / Ollama (design.txt §27);
infrastructure adapts them behind this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str


class LLMProvider(Protocol):

    def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        """One completion round.  Raises on transport errors only;
        prompt-level problems are the caller's concern."""
        ...
