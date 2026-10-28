"""0.6 Idempotency + tri-state execution results (K6).

Plan-v2 acceptance:
    test_same_key_applies_once
    test_result_tristate
"""

from __future__ import annotations

BOOM_SOURCE = """
def execute(ctx):
    raise TimeoutError("executor timed out")
"""


def _balance(platform, wid) -> object:
    gateway = platform.ctx.gateway_for(wid)
    obj = gateway.get_object("Vehicle/V001")
    return obj["properties"]["balance"]


def test_same_key_applies_once(platform, workspace_id):
    first = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="recharge-1")
    assert first["ok"] is True
    assert first["status"] == "succeeded"
    assert _balance(platform, workspace_id) == 105

    second = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="recharge-1")
    assert second["idempotent_replay"] is True
    assert second["execution_id"] == first["execution_id"]
    # the world changed exactly once
    assert _balance(platform, workspace_id) == 105

    # a different key is a different command -> the world changes again
    platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="recharge-2")
    assert _balance(platform, workspace_id) == 110


def test_failed_execution_is_recorded_and_replayed(platform, workspace_id):
    first = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.nope", target="Vehicle/V001",
        args={}, idempotency_key="bad-1")
    assert first["ok"] is False
    assert first["status"] == "failed"

    replay = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.nope", target="Vehicle/V001",
        args={}, idempotency_key="bad-1")
    assert replay["idempotent_replay"] is True
    assert replay["status"] == "failed"

    record = platform.ctx.repo.get("execution", workspace_id, "key_bad-1")
    assert record["execution_id"] == first["execution_id"]
    assert record["status"] == "failed"


def test_timeout_is_unknown_and_retried(platform, workspace_id):
    capability = platform.capabilities.create(
        workspace_id, "admin", key="boom", name="超时能力",
        target_type="Vehicle", description="timeout injection",
        implementation={"kind": "python", "source": BOOM_SOURCE})
    platform.capabilities.publish(workspace_id, capability["id"], "admin")

    result = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.boom", target="Vehicle/V001",
        args={}, idempotency_key="timeout-1")
    assert result["status"] == "unknown"
    # unknown is neither success nor failure
    assert result["ok"] is False
    assert result["error"]
    record = platform.ctx.repo.get("execution", workspace_id, "key_timeout-1")
    assert record["status"] == "unknown"

    # unknown is never auto-retried: the same key replays the recorded outcome
    retried = platform.capabilities.invoke(
        workspace_id, "admin", "Vehicle.boom", target="Vehicle/V001",
        args={}, idempotency_key="timeout-1")
    assert retried.get("idempotent_replay") is True
    assert retried["status"] == "unknown"

    # a human resolves the unknown explicitly before it stops blocking the key
    from onto_platform.application.execution_ledger import ExecutionLedger

    resolved = ExecutionLedger(platform.ctx).resolve(
        workspace_id, record["execution_id"], "admin", "not_applied",
        note="verified by operator")
    assert resolved["status"] == "unknown"
    assert resolved["resolution"]["outcome"] == "not_applied"
    assert resolved.get("status") == "unknown"


def test_idempotency_key_over_http(platform, workspace_id):
    """The HTTP door honours ``Idempotency-Key`` exactly like the use case."""
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from onto_platform.interfaces.api import build_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    url = f"{base}/api/workspaces/{workspace_id}/invoke"

    def invoke():
        request = urllib.request.Request(
            url,
            data=json.dumps({"function": "Vehicle.recharge",
                             "target": "Vehicle/V001",
                             "args": {"amount": 7}}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Idempotency-Key": "http-1"},
        )
        with urllib.request.urlopen(request) as res:
            return json.loads(res.read().decode("utf-8"))

    try:
        first = invoke()
        assert first["ok"] is True and first["status"] == "succeeded"
        assert _balance(platform, workspace_id) == 107
        second = invoke()
        assert second["idempotent_replay"] is True
        assert second["execution_id"] == first["execution_id"]
        assert _balance(platform, workspace_id) == 107
    finally:
        httpd.shutdown()
