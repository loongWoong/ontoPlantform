"""Phase 2.1: application objectives, deviation/trend and handling todos."""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError

from conftest import http_get, http_post, seed_workspace


def _app(platform, wid: str) -> dict:
    return platform.applications.create(wid, "admin", "收费监控")


def test_objective_is_declared_and_validated(platform):
    wid = seed_workspace(platform)
    app = _app(platform, wid)
    updated = platform.objectives.set(
        wid, app["id"], "admin", "success_rate", target=0.99, band=0.02,
        window=3600)
    objective = updated["objective"]
    assert objective["metric"] == "success_rate"
    assert objective["target"] == 0.99 and objective["band"] == 0.02
    assert objective["direction"] == "higher_is_better"
    assert objective["window"] == 3600
    with pytest.raises(ValidationError):
        platform.objectives.set(wid, app["id"], "admin", "not_a_metric",
                                target=1)
    with pytest.raises(ValidationError):
        platform.objectives.set(wid, app["id"], "admin", "success_rate",
                                target=1, band=-0.1)
    with pytest.raises(ValidationError):
        platform.objectives.set(wid, app["id"], "admin", "success_rate",
                                target=1, window=0)


def test_objective_reports_deviation_and_trend(platform):
    wid = seed_workspace(platform)
    app = _app(platform, wid)
    platform.objectives.set(wid, app["id"], "admin", "success_rate",
                            target=0.99, band=0.02)
    first = platform.objectives.evaluate(wid, app["id"], "admin")
    # no executions in the window yet: report unavailable, not a fake 1.0
    assert first["ok"] is False
    assert first["available"] is False
    assert first["current"] is None
    assert first["deviation"] is None

    platform.capabilities.invoke(wid, "admin", "Vehicle.nope")
    second = platform.objectives.evaluate(wid, app["id"], "admin")
    assert second["ok"] is False
    assert second["available"] is True
    assert second["current"] == pytest.approx(0.0)
    assert second["deviation"] == pytest.approx(-0.99)
    assert second["trend"] == "flat"
    assert second["trend_delta"] == pytest.approx(0.0)
    assert second["todo"]["status"] == "open"
    assert second["todo"]["application_id"] == app["id"]


def test_out_of_band_opens_one_todo_and_can_be_resolved(platform):
    wid = seed_workspace(platform)
    app = _app(platform, wid)
    platform.objectives.set(wid, app["id"], "admin", "success_rate",
                            target=0.99, band=0.02)
    platform.capabilities.invoke(wid, "admin", "Vehicle.nope")
    first = platform.objectives.evaluate(wid, app["id"], "admin")
    second = platform.objectives.evaluate(wid, app["id"], "admin")
    assert first["todo"]["id"] == second["todo"]["id"]
    open_todos = platform.objectives.list_todos(wid, "admin")
    assert len(open_todos) == 1
    events = platform.events.list(wid, event_type="application.objective_breached")
    assert len(events) == 1
    from onto_platform.errors import GateError

    with pytest.raises(GateError):
        platform.objectives.resolve_todo(wid, "admin", first["todo"]["id"])
    # closing is a claim about the world: bring it back in band first
    platform.objectives.set(wid, app["id"], "admin", "success_rate",
                            target=0.0, band=0.02)
    assert platform.objectives.evaluate(wid, app["id"], "admin")["ok"] is True
    resolved = platform.objectives.resolve_todo(
        wid, "admin", first["todo"]["id"], note="goal re-checked")
    assert resolved["status"] == "resolved" and resolved["resolved_by"] == "admin"
    assert platform.objectives.list_todos(wid, "admin") == []
    assert platform.objectives.list_todos(wid, "admin", status="all")[0]["status"] == "resolved"


def test_objective_over_http(platform, http_server):
    base, wid = http_server
    app = _app(platform, wid)
    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{app['id']}/objective",
        {"metric": "success_rate", "target": 0.99, "band": 0.02})
    assert status == 200, body
    assert body["objective"]["target"] == 0.99
    status, body = http_get(
        base, f"/api/workspaces/{wid}/applications/{app['id']}/objective")
    assert status == 200, body
    # GET is a read: before the first explicit evaluation nothing is claimed
    assert body["evaluated"] is False and body["ok"] is None
    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{app['id']}/objective/evaluate",
        {"user": "admin"})
    assert status == 200, body
    assert body["available"] is False and body["ok"] is False
    platform.capabilities.invoke(wid, "admin", "Vehicle.nope")
    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{app['id']}/objective/evaluate",
        {"user": "admin"})
    assert body["ok"] is False and body["current"] == 0.0
    status, todos = http_get(base, f"/api/workspaces/{wid}/todos?status=open")
    assert status == 200 and len(todos) == 1
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/todos/{todos[0]['id']}/resolve", {})
    assert status == 409
    # bring the objective back in band, then close it
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/applications/{app['id']}/objective",
        {"metric": "success_rate", "target": 0.0, "band": 0.02})
    assert status == 200
    http_post(base,
              f"/api/workspaces/{wid}/applications/{app['id']}/objective/evaluate",
              {"user": "admin"})
    status, resolved = http_post(
        base, f"/api/workspaces/{wid}/todos/{todos[0]['id']}/resolve",
        {"note": "re-checked"})
    assert status == 200 and resolved["status"] == "resolved"
