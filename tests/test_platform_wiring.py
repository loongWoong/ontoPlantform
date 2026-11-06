"""P1 integration A: the composition root wires Rule / Event / ApplicationSpec.

These tests go through the real ``build_platform`` fixture (not hand-built
facades) and call each P1 surface once, so a missing constructor argument or a
misnamed facade attribute fails here instead of in the REST layer.
"""

from __future__ import annotations

from onto_platform.bootstrap import (
    EventUseCases,
    Platform,
    RuleUseCases,
    build_platform,
)


def test_platform_exposes_p1_facades(platform):
    assert isinstance(platform, Platform)
    assert isinstance(platform.rules, RuleUseCases)
    assert isinstance(platform.business_events, EventUseCases)

    for attribute in ("create", "update", "publish", "disable", "delete",
                      "list", "evaluate"):
        assert callable(getattr(platform.rules, attribute))

    for attribute in ("create_contract", "publish_contract", "list_contracts",
                      "subscribe", "list_subscriptions",
                      "delete_subscription", "emit"):
        assert callable(getattr(platform.business_events, attribute))

    for attribute in ("get_spec", "add_interaction", "remove_interaction"):
        assert callable(getattr(platform.applications, attribute))


def test_business_events_share_the_platform_outbox(platform):
    # one EventLog: a second log would append a stream the dispatcher ignores
    emit_use_case = platform.business_events.emit
    assert getattr(emit_use_case, "_log") is platform.events


def test_platform_can_be_rebuilt_with_default_config():
    # the P1 fields are required dataclass fields: a hand-rolled Platform must
    # supply them, but build_platform must always do so without extra config
    rebuilt = build_platform()
    try:
        assert rebuilt.rules is not None
        assert rebuilt.business_events is not None
    finally:
        rebuilt.close()


def test_rule_facade_creates_lists_publishes_and_evaluates(platform,
                                                           workspace_id):
    created = platform.rules.create(
        workspace_id, "admin",
        name="余额非负", object_type="Vehicle",
        condition={"field": "balance", "op": "lt", "value": 0},
        message="余额不能为负")
    assert created["id"]
    assert created["lifecycle"] == "draft"

    listed = platform.rules.list(workspace_id, "admin")
    assert [rule["id"] for rule in listed] == [created["id"]]
    assert listed[0]["condition"] == {
        "field": "balance", "op": "lt", "value": 0}

    published = platform.rules.publish(workspace_id, "admin", created["id"])
    assert published["lifecycle"] == "published"

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")
    assert result["object_count"] == 2
    assert result["rule_count"] == 1
    assert result["passed"] is True
    assert result["violations"] == []


def test_event_facade_contract_publish_and_emit(platform, workspace_id):
    event_type = "traffic.vehicle_ping"
    aggregate_kind = "Vehicle"
    payload_schema = {
        "vehicle_id": {"type": "string", "required": True,
                       "description": "车辆"},
        "speed": {"type": "number", "required": False},
    }

    created = platform.business_events.create_contract(
        workspace_id, "admin", event_type, aggregate_kind, payload_schema,
        description="车辆心跳")
    assert created["name"] == event_type
    assert created["lifecycle"] == "draft"

    published = platform.business_events.publish_contract(
        workspace_id, "admin", event_type)
    assert published["lifecycle"] == "published"

    listed = platform.business_events.list_contracts(
        workspace_id, "admin", lifecycle="published")
    assert [row["name"] for row in listed] == [event_type]

    emitted = platform.business_events.emit(
        workspace_id, "admin", event_type, aggregate_kind, "Vehicle/V001",
        payload={"vehicle_id": "V001", "speed": 32.5})
    assert emitted["event_id"] == emitted["id"]
    assert emitted["event_id"].startswith("evt_")
    assert emitted["type"] == event_type
    assert emitted["aggregate"]["id"] == "Vehicle/V001"
    assert emitted["contract"] == event_type

    # the shared outbox sees it: proof the facade emits into Platform.events
    outbox = platform.events.list(workspace_id, event_type=event_type)
    assert [row["id"] for row in outbox] == [emitted["event_id"]]


def test_application_facade_spec_and_interactions(platform, workspace_id):
    app = platform.applications.create(
        workspace_id, "admin", "收费站监控", description="P1 组合根接线验证")
    assert app["id"]

    spec = platform.applications.get_spec(workspace_id, app["id"], "admin")
    assert set(spec) == {"spec", "diagnostics", "valid"}
    assert spec["valid"] is True
    assert spec["diagnostics"] == []
    assert spec["spec"]["application_id"] == app["id"]

    updated = platform.applications.add_interaction(
        workspace_id, app["id"], "admin", "approve", "workflow",
        title="审批")
    assert any(item["id"] == "approve" for item in updated["interactions"])

    with_interaction = platform.applications.get_spec(
        workspace_id, app["id"], "admin")
    assert any(item["id"] == "approve"
               for item in with_interaction["spec"]["interactions"])

    removed = platform.applications.remove_interaction(
        workspace_id, app["id"], "approve", "admin")
    assert all(item["id"] != "approve" for item in removed["interactions"])
