"""Phase 2.1/2.4: SLO targets, rate limits, workspace quota."""

from __future__ import annotations

import pytest

from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.errors import RateLimitError, ValidationError

from conftest import seed_workspace


def test_per_workspace_rate_limit_refuses_before_mutation():
    platform = build_platform(PlatformConfig(extra={"rate_limit_per_minute": 1}))
    wid = seed_workspace(platform)
    ok = platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5})
    assert ok["status"] == "succeeded"

    with pytest.raises(RateLimitError) as excinfo:
        platform.capabilities.invoke(
            wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
            args={"amount": 5})
    details = getattr(excinfo.value, "args", ("",))[0]
    assert "rate limit" in details
    # the refused call never reached the kernel: only the first change stuck
    vehicle = platform.ctx.gateway_for(wid).get_object("Vehicle/V001")
    assert vehicle["properties"]["balance"] == 105
    assert platform.metrics(wid, "admin")["indicators"]["executions_total"] == 1


def test_workspace_quota_limits_creation():
    platform = build_platform(PlatformConfig(extra={"max_workspaces": 1}))
    first = platform.workspaces.create("第一个", actor="admin")
    assert first["id"]
    with pytest.raises(ValidationError) as excinfo:
        platform.workspaces.create("第二个", actor="admin")
    assert "quota" in str(excinfo.value)


def test_slo_reports_indicators_and_breaches(platform):
    wid = seed_workspace(platform)
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001", args={"amount": 1})
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.nope", target="Vehicle/V001")

    slo = platform.service_levels(wid, "admin")
    indicators = slo["indicators"]
    assert indicators["success_rate"]["samples"] == 2
    assert indicators["success_rate"]["value"] == 0.5
    assert indicators["success_rate"]["ok"] is False
    assert slo["ok"] is False  # a 50% failure rate must breach the objective
    assert indicators["projection_age_seconds"]["ok"] is True


def test_slo_over_http(platform, http_server):
    base, wid = http_server
    from conftest import http_get

    status, payload = http_get(base, f"/api/workspaces/{wid}/slo")
    assert status == 200
    assert "success_rate" in payload["indicators"]
    assert payload["targets"]["success_rate"] == 0.99
