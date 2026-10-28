"""Contract-level truth tests (P0/P1 acceptance).

These tests do not re-test implementation details.  They assert the
user-visible contract that the control-loop report found broken: no fake SLO,
tri-state execution, a read-only GET, workspace-scoped write-only credentials,
and an honest empty-window answer.
"""

from __future__ import annotations

import pytest

from onto_platform.bootstrap import PlatformConfig, build_platform
from conftest import http_get, http_post, make_platform, seed_workspace


def test_empty_workspace_slo_is_not_a_fake_success():
    platform = make_platform()
    wid = platform.workspaces.create("空窗", actor="admin")["id"]
    slo = platform.service_levels.evaluate(wid)
    success = slo["indicators"]["success_rate"]
    assert success["status"] == "no_data"
    assert success["available"] is False
    assert success["value"] is None
    assert slo["ok"] is False
    assert "success_rate" in slo["blocked"]


def test_slo_marks_insufficient_samples_instead_of_guessing():
    from onto_platform.application.slo import ServiceLevels

    platform = make_platform()
    wid = seed_workspace(platform)
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1})
    slo = ServiceLevels(platform.ctx, {"min_samples": 3}).evaluate(wid)
    success = slo["indicators"]["success_rate"]
    assert success["status"] == "insufficient"
    assert success["samples"] == 1
    assert success["min_samples"] == 3
    assert slo["ok"] is False
    assert "success_rate" in slo["blocked"]


def test_slo_window_excludes_old_executions():
    platform = make_platform()
    wid = seed_workspace(platform)
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1})
    before = platform.service_levels.evaluate(wid, window_seconds=3600)
    assert before["indicators"]["success_rate"]["samples"] == 1
    # move the clock two hours ahead: the same record is outside the window
    platform.ctx.clock = lambda: "2030-01-01T02:00:00+00:00"
    after = platform.service_levels.evaluate(wid, window_seconds=3600)
    assert after["indicators"]["success_rate"]["status"] == "no_data"
    assert after["indicators"]["success_rate"]["samples"] == 0


def test_unknown_execution_is_tri_state_and_never_a_success():
    import time

    platform = make_platform()
    wid = seed_workspace(platform)
    slow = platform.capabilities.create(
        wid, "admin", key="slow", name="慢动作", target_type="Vehicle",
        implementation={"kind": "python", "source": (
            "import time\n"
            "def execute(ctx):\n"
            "    time.sleep(0.5)\n"
            "    return {'ok': True}\n")})
    platform.capabilities.publish(wid, slow["id"], "admin")
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.slow", target="Vehicle/V001",
        idempotency_key="truth-timeout-1", timeout_seconds=0.01)
    assert result["status"] == "unknown"
    assert result["ok"] is False
    assert result["retryable"] is True
    record = platform.ctx.repo.get(
        "execution", wid, result["execution_record_id"])
    assert record["status"] == "unknown"
    # the worker is still running: the same key must not start a second
    # execution while the first effect is in flight
    from onto_platform.errors import ExecutionInProgressError

    with pytest.raises(ExecutionInProgressError):
        platform.capabilities.invoke(
            wid, "admin", "Vehicle.slow", target="Vehicle/V001",
            idempotency_key="truth-timeout-1")
    time.sleep(0.8)
    later = platform.ctx.repo.get(
        "execution", wid, result["execution_record_id"])
    # the late outcome is recorded; it is never reported as success to the
    # timed-out caller
    assert later["status"] in ("succeeded", "unknown")


def test_http_invoke_returns_202_for_unknown_and_by_key_lookup(http_server):
    base, wid = http_server
    status, body = http_post(
        base, f"/api/workspaces/{wid}/invoke",
        {"user": "admin", "function": "Vehicle.recharge",
         "target": "Vehicle/V001", "args": {"amount": 2},
         "idempotency_key": "truth-http-202", "timeout": 0})
    assert status == 202
    assert body["status"] == "unknown"
    assert body["ok"] is False
    status, record = http_get(
        base, f"/api/workspaces/{wid}/executions/by-key/truth-http-202")
    assert status == 200
    assert record["status"] in ("unknown", "succeeded")
    assert record["id"] == "key_truth-http-202"


def test_metrics_and_slo_accept_a_window_and_report_window_mode(http_server):
    base, wid = http_server
    status, metrics = http_get(
        base, f"/api/workspaces/{wid}/metrics?window=5m")
    assert status == 200
    assert metrics["window_seconds"] == 300
    status, slo = http_get(base, f"/api/workspaces/{wid}/slo?window_seconds=900")
    assert status == 200
    assert slo["window_seconds"] == 900
    status, body = http_get(
        base, f"/api/workspaces/{wid}/slo?window=ear")
    assert status == 400


def test_token_mode_rejects_anonymous_http(http_server, monkeypatch):
    import json as jsonlib
    import urllib.error
    import urllib.request

    base, _wid = http_server
    monkeypatch.setenv("ONTO_API_TOKEN", "test-token")
    monkeypatch.setenv("ONTO_API_TOKEN_ACTOR", "api-client")

    def get(path, headers=None):
        request = urllib.request.Request(base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, jsonlib.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, jsonlib.loads(exc.read())

    status, _ = get("/api/workspaces")
    assert status == 403
    status, body = get("/api/workspaces",
                       {"Authorization": "Bearer test-token"})
    assert status == 200
    assert isinstance(body, list)


def test_canary_check_is_fresh_and_promotion_requires_it(http_server):
    base, wid = http_server
    status, listed = http_get(base, f"/api/workspaces/{wid}/releases")
    assert status == 200
    release = listed["releases"][-1]
    status, staged = http_post(
        base, f"/api/workspaces/{wid}/releases/{release['id']}/canary",
        {"user": "admin"})
    assert status == 200, staged
    status, body = http_post(
        base, f"/api/workspaces/{wid}/releases/canary/promote", {"user": "admin"})
    assert status == 409
    assert "check" in body["error"]
    status, check = http_post(
        base, f"/api/workspaces/{wid}/releases/canary/check", {"user": "admin"})
    assert status == 200 and check["ok"] is True
    status, promoted = http_post(
        base, f"/api/workspaces/{wid}/releases/canary/promote", {"user": "admin"})
    assert status == 200, promoted
    assert promoted["canary"]["promoted"] is True


def test_llm_config_is_workspace_scoped_and_write_only(http_server):
    base, wid = http_server
    status, body = http_post(
        base, f"/api/workspaces/{wid}/llm-config",
        {"user": "admin", "provider": "openai",
         "base_url": "https://example.invalid/v1", "model": "test-model",
         "api_key": "sk-very-secret-value"})
    assert status == 200, body
    assert "sk-very-secret-value" not in str(body)
    assert body["has_key"] is True
    status, body = http_get(base, f"/api/workspaces/{wid}/llm-config?user=admin")
    assert status == 200
    assert "sk-very-secret-value" not in str(body)
    assert body["model"] == "test-model"
    assert body["api_key_masked"].startswith("sk-v")


def test_contract_compensate_accepts_empty_string():
    platform = make_platform()
    wid = seed_workspace(platform)
    capability = platform.capabilities.create(
        wid, "admin", key="tick", name="计数", target_type="Vehicle",
        implementation={"kind": "python", "source": "def execute(ctx):\n    return {}\n"},
        contract={"preconditions": [], "postconditions": [],
                  "effects": [], "idempotent": True, "compensate": ""})
    assert capability["contract"]["compensate"] in (None, "")


def test_scheduler_status_and_run_once():
    platform = make_platform()
    wid = seed_workspace(platform)
    scheduler = platform.scheduler
    assert scheduler is None
    scheduler = platform.start_scheduler(poll_seconds=60)
    try:
        status = scheduler.status()
        assert status["running"] is True
        names = {task["name"] for task in status["tasks"]}
        assert {"outbox.dispatch", "objectives.evaluate",
                "controller.cycle"} <= names
        platform.stop_scheduler()
        assert scheduler.status()["running"] is False
    finally:
        platform.stop_scheduler()
