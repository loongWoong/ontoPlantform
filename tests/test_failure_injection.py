"""0.1/0.7 failure injection: the controller must degrade predictably.

Plan-v2 acceptance anchors:
    test_llm_timeout_is_unknown   (timeout must not be rendered as failure)
    test_bad_json_does_not_crash
    test_provider_429_is_a_failed_run
    test_max_steps_is_bounded
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from onto_platform.application.agent_runner import AgentRunner

from fakes import FakeClock, FakeIdFactory, FakeProvider


def _gateway():
    return SimpleNamespace(
        describe=lambda: {"objects": [], "relations": [], "functions": []},
        describe_object=lambda name: {"name": name},
        find_objects=lambda t, f=None: [],
        related=lambda ref, rel, direction="out": [],
        get_object=lambda ref: None,
        invoke=lambda function, target=None, args=None: SimpleNamespace(
            ok=True, status="succeeded", output={}, error=None, changes=()),
    )


def test_bad_json_does_not_crash():
    provider = FakeProvider(["not json"] * 3)
    run = AgentRunner(_gateway(), provider, "", max_steps=4).run("go")
    assert run.ok is False
    assert "合法 JSON" in run.answer
    assert len(provider.calls) == 3


def test_empty_content_counts_as_invalid_reply():
    provider = FakeProvider([""] * 2 + ['{"think": "done", "answer": "ok"}'])
    run = AgentRunner(_gateway(), provider, "", max_steps=4).run("go")
    assert run.ok is True
    assert run.answer == "ok"


def test_provider_429_is_a_failed_run():
    provider = FakeProvider(mode="429")
    run = AgentRunner(_gateway(), provider, "", max_steps=4).run("go")
    assert run.ok is False
    assert "429" in run.answer


def test_provider_500_is_a_failed_run_not_a_crash():
    provider = FakeProvider(mode="500")
    run = AgentRunner(_gateway(), provider, "", max_steps=4).run("go")
    assert run.ok is False
    assert "500" in run.answer


def test_llm_timeout_is_unknown_not_failure():
    """A timed-out controller turn propagates: the effect is unknown."""
    provider = FakeProvider(mode="timeout")
    with pytest.raises(TimeoutError):
        AgentRunner(_gateway(), provider, "", max_steps=4).run("go")


def test_unknown_tool_becomes_an_observation_not_a_crash():
    provider = FakeProvider([
        '{"think": "try", "action": {"tool": "nope", "args": {}}}',
        '{"think": "done", "answer": "survived"}',
    ])
    run = AgentRunner(_gateway(), provider, "", max_steps=4).run("go")
    assert run.ok is True
    assert run.answer == "survived"
    assert "unknown tool" in run.steps[0].observation


def test_max_steps_is_bounded():
    runner = AgentRunner(_gateway(), FakeProvider(), "", max_steps=10_000)
    assert runner.max_steps == 20
    assert AgentRunner(_gateway(), FakeProvider(), "", max_steps=0).max_steps == 1


def test_seams_are_deterministic():
    clock, ids = FakeClock(), FakeIdFactory()
    assert clock() == "2026-01-01T00:00:00+00:00"
    assert (ids(), ids()) == ("id-1", "id-2")
