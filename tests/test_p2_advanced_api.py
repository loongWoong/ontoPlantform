"""P2.3-P2.6 REST wiring: sandbox, eval sets, schema registry, bridge, twin.

The services own their validation and permissions; these tests prove the HTTP
surfaces are mounted, use the shared workspace context, and keep the additive
response shapes stable.
"""

from __future__ import annotations

from conftest import http_delete, http_get, http_post
from onto_platform.application.smartui.sandbox import SandboxResult


def test_digital_twin_snapshot_simulate_and_policies(http_server):
    base, wid = http_server
    status, snapshot = http_get(
        base, f"/api/workspaces/{wid}/twin/snapshot"
              "?ref=Vehicle%2FV001&depth=1")
    assert status == 200, snapshot
    refs = {node["ref"] for node in snapshot["nodes"]}
    assert refs == {"Vehicle/V001", "TollStation/ST001", "TollStation/ST002"}
    assert len(snapshot["edges"]) == 2
    assert snapshot["capabilities"]["sideEffects"] is False

    status, simulated = http_post(
        base, f"/api/workspaces/{wid}/twin/simulate",
        {"ref": "Vehicle/V001", "changes": {"balance": 100}})
    assert status == 200, simulated
    assert simulated["noSideEffects"] is True
    assert simulated["changes"] == {"balance": 100}

    status, policy = http_post(
        base, f"/api/workspaces/{wid}/twin/policies",
        {"name": "station-status", "source_type": "Vehicle",
         "relation": "passes", "target_field": "status", "mode": "copy"})
    assert status == 201, policy
    assert policy["mode"] == "copy"

    status, listed = http_get(base, f"/api/workspaces/{wid}/twin/policies")
    assert status == 200, listed
    assert [row["name"] for row in listed["policies"]] == ["station-status"]

    status, deleted = http_delete(
        base, f"/api/workspaces/{wid}/twin/policies/{policy['id']}")
    assert status == 200, deleted
    assert deleted["deleted"] is True


def test_eval_set_rest_roundtrip(http_server):
    base, wid = http_server
    status, created = http_post(
        base, f"/api/workspaces/{wid}/eval-sets",
        {"name": "API smoke", "cases": [
            {"id": "c1", "name": "missing generation",
             "question": "no such question"}]})
    assert status == 201, created
    assert created["cases"][0]["id"] == "c1"

    status, listed = http_get(base, f"/api/workspaces/{wid}/eval-sets")
    assert status == 200, listed
    assert [row["name"] for row in listed["evalSets"]] == ["API smoke"]

    status, replayed = http_post(
        base, f"/api/workspaces/{wid}/eval-sets/{created['id']}/replay")
    assert status == 200, replayed
    assert replayed["summary"]["total"] == 1
    assert replayed["summary"]["passed"] == 0
    assert replayed["cases"][0]["error"] == "no matching generation"

    status, deleted = http_delete(
        base, f"/api/workspaces/{wid}/eval-sets/{created['id']}")
    assert status == 200, deleted
    assert deleted["deleted"] is True


def test_event_schema_registry_rest(http_server):
    base, wid = http_server
    status, registered = http_post(
        base, f"/api/workspaces/{wid}/event-schemas",
        {"event_type": "todo.opened", "version": 1,
         "payload_schema": {"id": {"type": "string"}}})
    assert status == 201, registered
    assert registered["version"] == 1

    status, fetched = http_get(
        base, f"/api/workspaces/{wid}/event-schemas/todo.opened")
    assert status == 200, fetched
    assert fetched["version"] == 1
    assert fetched["payload_schema"]["id"]["type"] == "string"

    status, checked = http_post(
        base, f"/api/workspaces/{wid}/event-schemas/compatibility",
        {"event_type": "todo.opened",
         "payload_schema": {"id": {"type": "string"},
                            "note": {"type": "string"}}})
    assert status == 200, checked
    assert checked["compatible"] is True
    assert checked["added"] == ["note"]

    status, rejected = http_post(
        base, f"/api/workspaces/{wid}/event-schemas",
        {"event_type": "todo.opened", "version": 2,
         "payload_schema": {}})
    assert status == 400, rejected


def test_event_bridge_rest_lifecycle(http_server, platform):
    base, wid = http_server
    status, bridge = http_post(
        base, f"/api/workspaces/{wid}/event-bridges",
        {"name": "api-bridge", "event_types": ["todo.opened"],
         "url": "https://example.com/p2-bridge"})
    assert status == 201, bridge
    assert bridge["active"] is True
    assert bridge["webhook_id"]

    status, listed = http_get(base, f"/api/workspaces/{wid}/event-bridges")
    assert status == 200, listed
    assert [row["name"] for row in listed["bridges"]] == ["api-bridge"]

    status, paused = http_post(
        base, f"/api/workspaces/{wid}/event-bridges/{bridge['id']}/pause")
    assert status == 200, paused
    assert paused["active"] is False

    status, activated = http_post(
        base, f"/api/workspaces/{wid}/event-bridges/{bridge['id']}/activate")
    assert status == 200, activated
    assert activated["active"] is True

    status, deleted = http_delete(
        base, f"/api/workspaces/{wid}/event-bridges/{bridge['id']}")
    assert status == 200, deleted
    assert deleted["webhook_deactivated"] is True


def test_plugin_sandbox_rest_returns_isolation(http_server, monkeypatch):
    base, wid = http_server
    from onto_platform.application.smartui import sandbox as sandbox_module

    def fake_run_package(*args, **kwargs):
        return SandboxResult(
            ok=True, result={"ok": True}, error="", exit_code=0,
            timed_out=False, duration_ms=3, stdout_excerpt="",
            stderr_excerpt="",
            isolation=dict(sandbox_module._isolation()),
            resource_limits=dict(
                sandbox_module._resource_limits(1.0, 64)))

    # The HTTP handler and this test share the same SandboxUseCases instance;
    # patching the class method keeps the real request/response wiring under
    # test without spawning a child process for a dummy package.
    monkeypatch.setattr(
        sandbox_module.SandboxUseCases, "run_package", fake_run_package)
    status, result = http_post(
        base, f"/api/workspaces/{wid}/plugins/sandbox/run",
        {"package_dir": "unused", "signature": "unused"})
    assert status == 200, result
    assert result["ok"] is True
    assert result["isolation"]["process"] is True
    assert result["isolation"]["network"] == "not-enforced"
