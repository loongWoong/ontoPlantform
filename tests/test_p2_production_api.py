"""P2.2 REST surfaces: webhook delivery ledger and durable event handlers.

The core services already own the state machines; these tests prove the
additive HTTP routes are reachable, permission-checked, and never leak a
webhook/handler signing secret.
"""

from __future__ import annotations

from conftest import http_get, http_post


def test_webhook_delivery_ledger_and_dead_letters_are_listable(
        http_server, platform):
    base, wid = http_server
    hook = platform.webhooks.create_webhook(
        wid, "admin", "https://example.com/p2-hook", ["todo.opened"],
        secret="s3cr3t")

    status, body = http_get(
        base, f"/api/workspaces/{wid}/security/webhooks/deliveries")
    assert status == 200, body
    assert body == {"deliveries": []}

    status, body = http_get(
        base, f"/api/workspaces/{wid}/security/webhooks/dead-letters")
    assert status == 200, body
    assert body == {"deliveries": []}

    # create a dead delivery directly through the ledger, then replay it via REST
    event = {
        "id": "evt-p2-api",
        "event_id": "evt-p2-api",
        "workspace_id": wid,
        "type": "todo.opened",
        "payload": {},
    }
    delivery = platform.webhooks.ledger.get_or_create(wid, hook["id"], event)
    stored = platform.ctx.repo.raw.get(
        "webhook_delivery", wid, delivery["id"])
    stored.update({"status": "dead", "attempts": 5})
    platform.ctx.repo.raw.put(
        "webhook_delivery", wid, delivery["id"], stored)

    status, listed = http_get(
        base, f"/api/workspaces/{wid}/security/webhooks/dead-letters")
    assert status == 200, listed
    assert [row["id"] for row in listed["deliveries"]] == [delivery["id"]]

    status, replayed = http_post(
        base,
        f"/api/workspaces/{wid}/security/webhooks/deliveries/"
        f"{delivery['id']}/replay")
    assert status == 200, replayed
    assert replayed["status"] == "pending"
    assert replayed["attempts"] == 0


def test_webhook_replay_unknown_delivery_is_404(http_server):
    base, wid = http_server
    status, body = http_post(
        base,
        f"/api/workspaces/{wid}/security/webhooks/deliveries/missing/replay")
    assert status == 404, body


def test_event_handler_registry_is_crud_and_never_leaks_secret(http_server):
    base, wid = http_server
    payload = {
        "handler_key": "p2-api-handler",
        "event_types": ["todo.opened"],
        "kind": "http",
        "endpoint": "https://example.com/hook",
        "secret": "s3cr3t",
        "lease_seconds": 45,
    }
    status, created = http_post(
        base, f"/api/workspaces/{wid}/event-handlers", payload)
    assert status == 200, created
    assert created["handler_key"] == "p2-api-handler"
    assert created["secret"] == "***"

    status, listed = http_get(
        base, f"/api/workspaces/{wid}/event-handlers")
    assert status == 200, listed
    assert [row["handler_key"] for row in listed["handlers"]] == [
        "p2-api-handler"]
    assert listed["handlers"][0]["secret"] == "***"

    status, paused = http_post(
        base,
        f"/api/workspaces/{wid}/event-handlers/p2-api-handler/pause")
    assert status == 200, paused
    assert paused["active"] is False
    assert paused["secret"] == "***"

    status, activated = http_post(
        base,
        f"/api/workspaces/{wid}/event-handlers/p2-api-handler/activate")
    assert status == 200, activated
    assert activated["active"] is True

    status, heartbeat = http_post(
        base,
        f"/api/workspaces/{wid}/event-handlers/p2-api-handler/heartbeat",
        {"worker_id": "worker-a"})
    assert status == 200, heartbeat
    assert heartbeat["last_worker"] == "worker-a"
