"""Phase 1.9: metrics, find pagination, change-stream retention."""

from __future__ import annotations

import pytest

from onto_platform.infrastructure.runtime import kernel_gateway

from conftest import http_get, seed_workspace


def test_metrics_indicators(platform):
    wid = seed_workspace(platform)
    metrics = platform.metrics(wid, "admin")
    indicators = metrics["indicators"]
    assert indicators["objects_total"] == 4  # 2 vehicles + 2 stations
    assert indicators["capabilities_total"] >= 1
    assert indicators["executions_total"] == 0
    assert indicators["events_pending"] >= 1  # publish events await dispatch
    assert metrics["deployment"]["release_id"]
    assert isinstance(indicators["changes_total"], int)


def test_metrics_counts_failed_executions(platform):
    wid = seed_workspace(platform)
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1})  # Vehicle.recharge needs amount; succeeds
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.nope", target="Vehicle/V001")
    metrics = platform.metrics(wid, "admin")["indicators"]
    assert metrics["executions_total"] == 2
    assert metrics["executions_failed"] == 1


def test_metrics_over_http(platform, http_server):
    base, wid = http_server
    status, payload = http_get(base, f"/api/workspaces/{wid}/metrics")
    assert status == 200 and payload["indicators"]["objects_total"] == 4


def test_find_limit_cursor(platform, http_server):
    base, wid = http_server
    status, payload = http_get(
        base, f"/api/workspaces/{wid}/state/find?type=Vehicle&limit=1&offset=0&report=1")
    assert status == 200
    assert payload["total"] == 2 and len(payload["objects"]) == 1
    first = payload["objects"][0]["id"]
    status, payload = http_get(
        base, f"/api/workspaces/{wid}/state/find?type=Vehicle&limit=1&offset=1&report=1")
    assert payload["total"] == 2
    assert payload["objects"][0]["id"] != first


def test_change_log_compacts_to_retention_window(platform, monkeypatch):
    monkeypatch.setattr(kernel_gateway, "CHANGE_LOG_MAX", 8)
    monkeypatch.setattr(kernel_gateway, "CHANGE_LOG_KEEP", 4)
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    for i in range(12):
        platform.capabilities.invoke(
            wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
            args={"amount": 1})
    assert len(gateway._changes) <= 8
    assert len(gateway._changes) >= 4
    # the cursor boundary only moves forward: old cursors must resync
    assert gateway.oldest_change_seq() > 0
    newer = gateway.observe_changes(gateway.oldest_change_seq())
    assert all(e["seq"] > gateway.oldest_change_seq() for e in newer)


def test_changes_endpoint_supports_limit(platform, http_server):
    base, wid = http_server
    status, payload = http_get(base, f"/api/workspaces/{wid}/changes?limit=1")
    assert status == 200 and (len(payload) == 1 or payload == [])
