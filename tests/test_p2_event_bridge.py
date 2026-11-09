"""P2.6: event bridge — external delivery through the webhook ledger."""

from __future__ import annotations

import pytest

from onto_platform.application.event_bridge import (
    BRIDGE_KINDS,
    BRIDGE_KIND,
    EventBridgeService,
)
from onto_platform.errors import AccessDeniedError, NotFoundError, ValidationError


class RecordingTransport:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url: str, body: bytes, headers: dict,
                 timeout: float) -> int:
        self.calls.append({"url": url, "body": body,
                           "headers": dict(headers)})
        return self.status


def _bridge(platform, transport: RecordingTransport | None = None):
    if transport is not None:
        platform.webhooks.transport = transport
    return EventBridgeService(platform.ctx, webhooks=platform.webhooks)


def _publish_contract(platform, workspace_id: str,
                      name: str = "traffic.vehicle_ping") -> str:
    platform.business_events.create_contract(
        workspace_id, "admin", name, "Vehicle",
        {"vid": {"type": "string", "required": True},
         "speed": {"type": "number"}})
    platform.business_events.publish_contract(workspace_id, "admin", name)
    return name


def _emit(platform, workspace_id: str, name: str) -> dict:
    return platform.business_events.emit(
        workspace_id, "admin", name, "Vehicle", "V001",
        {"vid": "V001", "speed": 88})


# ----------------------------------------------------------------------
# create: internal webhook wiring and published-contract validation
# ----------------------------------------------------------------------


def test_create_bridge_wires_an_internal_webhook(platform, workspace_id):
    name = _publish_contract(platform, workspace_id)
    bridge = _bridge(platform)

    row = bridge.create(
        workspace_id, "admin", "ext-sink", [name],
        "https://hooks.example.test/bridge", "s3cret",
        field_map={"speed": "payload.speed"})

    assert row["id"].startswith("bridge_")
    assert row["workspace_id"] == workspace_id
    assert row["name"] == "ext-sink"
    assert row["kind"] == "webhook"
    assert row["event_types"] == [name]
    assert row["active"] is True
    assert row["field_map"] == {"speed": "payload.speed"}
    assert row["include_envelope"] is True
    assert row["webhook_id"]

    hook = platform.webhooks.get_webhook(
        workspace_id, row["webhook_id"], actor="admin")
    assert hook["source_bridge_id"] == row["id"]
    assert hook["event_types"] == [name]

    assert bridge.get(workspace_id, "admin", row["id"])["id"] == row["id"]
    assert [item["id"] for item in bridge.list(workspace_id, "admin")] == \
        [row["id"]]
    # Internal (dispatcher) listing needs no actor.
    assert [item["id"] for item in bridge.list(workspace_id)] == [row["id"]]

    # Platform lifecycle events are allowed without a business contract.
    lifecycle = bridge.create(
        workspace_id, "admin", "lifecycle", ["workspace.created"],
        "https://hooks.example.test/lifecycle")
    assert lifecycle["event_types"] == ["workspace.created"]

    with pytest.raises(NotFoundError):
        bridge.get(workspace_id, "admin", "bridge_missing")


def test_create_requires_published_contracts(platform, workspace_id):
    platform.business_events.create_contract(
        workspace_id, "admin", "traffic.draft_event", "Vehicle",
        {"vid": {"type": "string"}})
    bridge = _bridge(platform)

    with pytest.raises(ValidationError, match="not published"):
        bridge.create(workspace_id, "admin", "draft",
                      ["traffic.draft_event"],
                      "https://hooks.example.test/draft")
    with pytest.raises(ValidationError, match="unknown"):
        bridge.create(workspace_id, "admin", "unknown",
                      ["no.such.event"], "https://hooks.example.test/unknown")

    name = _publish_contract(platform, workspace_id)
    with pytest.raises(ValidationError, match="duplicate"):
        bridge.create(workspace_id, "admin", "dup", [name, name],
                      "https://hooks.example.test/dup")
    with pytest.raises(ValidationError):
        bridge.create(workspace_id, "admin", "empty", [],
                      "https://hooks.example.test/empty")
    with pytest.raises(ValidationError):
        bridge.create(workspace_id, "admin", "bad-map", [name],
                      "https://hooks.example.test/map",
                      field_map={"speed": "payload..speed"})

    # Rejected creations must not leave a webhook behind.
    assert platform.webhooks.list_webhooks(workspace_id) == []


# ----------------------------------------------------------------------
# transform
# ----------------------------------------------------------------------


def test_transform_maps_payload_and_missing_paths_to_none(platform,
                                                         workspace_id):
    bridge = _bridge(platform)
    event = {
        "id": "evt_1",
        "event_id": "evt_1",
        "type": "traffic.vehicle_ping",
        "event_version": 2,
        "ts": "2026-01-01T00:00:00+00:00",
        "workspace_id": workspace_id,
        "payload": {"vid": "V001", "speed": 88,
                    "nested": {"city": "SZ"}},
    }
    config = {
        "field_map": {
            "speed": "payload.speed",
            "city": "payload.nested.city",
            "missing": "payload.nope",
        },
        "include_envelope": True,
    }
    out = bridge.transform(event, config)
    assert set(out) == {"id", "type", "version", "occurredAt", "workspaceId",
                        "payload"}
    assert out["id"] == "evt_1"
    assert out["type"] == "traffic.vehicle_ping"
    assert out["version"] == 2
    assert out["occurredAt"] == "2026-01-01T00:00:00+00:00"
    assert out["workspaceId"] == workspace_id
    assert out["payload"] == {"speed": 88, "city": "SZ", "missing": None}

    flat = bridge.transform(
        event, {"field_map": {"speed": "payload.speed"},
                "include_envelope": False})
    assert flat == {"speed": 88}

    identity = bridge.transform(event)
    assert identity["payload"] == event["payload"]
    assert identity["payload"] is not event["payload"]
    assert identity["version"] == 2


def test_transform_accepts_a_persisted_bridge(platform, workspace_id):
    name = _publish_contract(platform, workspace_id)
    bridge = _bridge(platform)
    row = bridge.create(
        workspace_id, "admin", "ext", [name],
        "https://hooks.example.test/transform",
        field_map={"vid": "payload.vid"}, include_envelope=False)
    event = _emit(platform, workspace_id, name)

    assert bridge.transform(event, row["id"]) == {"vid": "V001"}
    with pytest.raises(NotFoundError):
        bridge.transform(event, "bridge_missing")


# ----------------------------------------------------------------------
# update / delete
# ----------------------------------------------------------------------


def test_update_syncs_the_internal_webhook(platform, workspace_id):
    name = _publish_contract(platform, workspace_id)
    bridge = _bridge(platform)
    row = bridge.create(
        workspace_id, "admin", "ext", [name],
        "https://hooks.example.test/update",
        field_map={"speed": "payload.speed"})

    updated = bridge.update(
        workspace_id, "admin", row["id"], name="renamed", active=False,
        field_map={"vid": "payload.vid"}, include_envelope=False)
    assert updated["name"] == "renamed"
    assert updated["active"] is False
    assert updated["include_envelope"] is False
    assert updated["field_map"] == {"vid": "payload.vid"}
    hook = platform.webhooks.get_webhook(
        workspace_id, row["webhook_id"], actor="admin")
    assert hook["active"] is False

    reactivated = bridge.update(workspace_id, "admin", row["id"],
                                active=True)
    assert reactivated["active"] is True
    assert platform.webhooks.get_webhook(
        workspace_id, row["webhook_id"], actor="admin")["active"] is True

    renamed_type = bridge.update(
        workspace_id, "admin", row["id"], event_types=["workspace.created"])
    assert renamed_type["event_types"] == ["workspace.created"]
    assert platform.webhooks.get_webhook(
        workspace_id, row["webhook_id"], actor="admin")["event_types"] == \
        ["workspace.created"]
    with pytest.raises(ValidationError):
        bridge.update(workspace_id, "admin", row["id"],
                      event_types=["no.such.event"])


def test_delete_deactivates_webhook_but_keeps_ledger_rows(platform,
                                                         workspace_id):
    name = _publish_contract(platform, workspace_id)
    transport = RecordingTransport()
    bridge = _bridge(platform, transport)
    row = bridge.create(
        workspace_id, "admin", "ext", [name],
        "https://hooks.example.test/delete")
    _emit(platform, workspace_id, name)
    assert bridge.dispatch(workspace_id)["delivered"] == 1
    ledger_before = platform.webhooks.list_deliveries(workspace_id)
    assert len(ledger_before) == 1

    result = bridge.delete(workspace_id, "admin", row["id"])
    assert result == {"id": row["id"], "deleted": True,
                      "webhook_deactivated": True}
    hook = platform.webhooks.get_webhook(
        workspace_id, row["webhook_id"], actor="admin")
    assert hook["active"] is False
    stored = bridge.get(workspace_id, "admin", row["id"])
    assert stored["active"] is False
    assert stored["deleted_at"]
    # Delivery history is audit data: deleting a bridge must not drop it.
    assert len(platform.webhooks.list_deliveries(workspace_id)) == \
        len(ledger_before)
    assert BRIDGE_KIND == "event_bridge"
    assert BRIDGE_KINDS == ("webhook", "http")


# ----------------------------------------------------------------------
# dispatch reuses the webhook ledger
# ----------------------------------------------------------------------


def test_dispatch_reuses_webhook_ledger_and_delivers_once(platform,
                                                          workspace_id):
    name = _publish_contract(platform, workspace_id)
    transport = RecordingTransport()
    bridge = _bridge(platform, transport)
    row = bridge.create(
        workspace_id, "admin", "ext", [name],
        "https://hooks.example.test/dispatch", "s3cret")
    event = _emit(platform, workspace_id, name)

    first = bridge.dispatch(workspace_id)
    assert first["delivered"] == 1
    assert first["failed"] == 0
    assert first["bridges"] == 1
    assert len(transport.calls) == 1

    deliveries = platform.webhooks.list_deliveries(workspace_id)
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "delivered"
    assert deliveries[0]["webhook_id"] == row["webhook_id"]
    assert deliveries[0]["event_id"] == event["event_id"]

    stored_event = next(
        item for item in platform.ctx.repo.list("event", workspace_id)
        if item.get("event_id") == event["event_id"])
    assert stored_event["webhook_delivered_at"]

    # Second pass: the ledger row is terminal, so nothing is re-delivered.
    second = bridge.dispatch(workspace_id)
    assert second["delivered"] == 0
    assert len(transport.calls) == 1
    assert len(platform.webhooks.list_deliveries(workspace_id)) == 1

    actions = [entry["action"]
               for entry in platform.ctx.audit.list(workspace_id)]
    assert "event_bridge.dispatched" in actions


def test_inactive_bridge_is_not_dispatched(platform, workspace_id):
    name = _publish_contract(platform, workspace_id)
    transport = RecordingTransport()
    bridge = _bridge(platform, transport)
    bridge.create(workspace_id, "admin", "ext", [name],
                  "https://hooks.example.test/off", active=False)
    _emit(platform, workspace_id, name)

    result = bridge.dispatch(workspace_id)
    assert result["delivered"] == 0
    assert result["bridges"] == 0
    assert transport.calls == []


# ----------------------------------------------------------------------
# authorization
# ----------------------------------------------------------------------


def test_bridge_management_requires_security_manage(platform, workspace_id):
    name = _publish_contract(platform, workspace_id)
    bridge = _bridge(platform)
    row = bridge.create(workspace_id, "admin", "ext", [name],
                        "https://hooks.example.test/perm")
    platform.workspaces.add_member(workspace_id, "bob", "viewer",
                                   actor="admin")

    with pytest.raises(AccessDeniedError):
        bridge.create(workspace_id, "bob", "evil", [name],
                      "https://hooks.example.test/evil")
    with pytest.raises(AccessDeniedError):
        bridge.list(workspace_id, "bob")
    with pytest.raises(AccessDeniedError):
        bridge.get(workspace_id, "bob", row["id"])
    with pytest.raises(AccessDeniedError):
        bridge.update(workspace_id, "bob", row["id"], name="evil")
    with pytest.raises(AccessDeniedError):
        bridge.delete(workspace_id, "bob", row["id"])

    # The internal dispatcher path still lists without an actor.
    assert bridge.list(workspace_id)
