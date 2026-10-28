"""Programmable test doubles (plan-v2 0.1 seams).

Kept deliberately dependency-free: the platform has zero runtime deps and
its tests should not need network, plugins or extra packages.
"""

from __future__ import annotations

from onto_platform.ports.llm import LLMResponse


class FakeProvider:
    """LLM provider double with scripted or failing behaviour.

    ``mode`` injects one transport failure: ``timeout`` (raises TimeoutError,
    which must surface as an ``unknown`` execution), ``429`` (retryable
    transport error) or ``bad_json``/``empty`` (prompt-level misses).
    """

    def __init__(self, script=None, *, mode: str | None = None) -> None:
        self.script = list(script or [])
        self.mode = mode
        self.calls: list[list] = []

    def complete(self, messages):
        self.calls.append(list(messages))
        if self.mode == "timeout":
            raise TimeoutError("LLM provider timed out")
        if self.mode == "429":
            raise RuntimeError("429 Too Many Requests")
        if self.mode == "500":
            raise RuntimeError("500 Internal Server Error")
        if self.mode == "bad_json":
            return LLMResponse(content="this is definitely not json")
        if self.mode == "empty":
            return LLMResponse(content="")
        if not self.script:
            raise AssertionError("FakeProvider script exhausted")
        return LLMResponse(content=self.script.pop(0))


class FakeClock:
    def __init__(self, start: str = "2026-01-01T00:00:00+00:00") -> None:
        self.value = start

    def __call__(self) -> str:
        return self.value


class FakeIdFactory:
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self) -> str:
        self.counter += 1
        return f"id-{self.counter}"
