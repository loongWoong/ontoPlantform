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
    # Optional provider-reported usage (prompt/completion/total tokens).  It is
    # deliberately optional so scripted/test providers stay source-compatible;
    # real OpenAI-compatible endpoints do return it and cost/observability code
    # must not have to re-parse the raw response.
    usage: dict | None = None


class LLMTransportError(Exception):
    """A model call failed before a usable completion was produced.

    Callers should keep the machine-readable ``kind``/``status`` and show a
    human-readable message.  The raw provider body is truncated; credentials
    are never included by the provider adapter.
    """

    def __init__(self, message: str, *, kind: str = "transport",
                 status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.body = body


class LLMProvider(Protocol):

    def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        """One completion round.  Raises on transport errors only;
        prompt-level problems are the caller's concern."""
        ...
