"""Business event chain: contracts, subscriptions, EmitEvent and delivery.

Uses the shared ``platform`` / ``workspace_id`` fixtures (seeded traffic
workspace) and drives the use cases directly - facade/API wiring is the
composition root's job.  Platform lifecycle events (EVENT_CATALOG) and their
outbox/Dispatcher behaviour must keep working unchanged; the last test pins
that regression.

Required commands:
    python -m pytest tests/test_event_contract.py tests/test_event_subscriptions.py \
        tests/test_outbox_delivery.py -q
    python -m pytest -q
"""

from __future__ import annotations

import pytest

from onto_platform.application.event_usecases import (
    CreateEventContract,
    DeleteEventSubscription,
    EmitEvent,
    ListEventContracts,
    ListEventSubscriptions,
    PublishEventContract,
    SubscribeEvent,
)
from onto_platform.domain.event import EventContract
from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)

EVENT_TYPE = "traffic.vehicle_entered"
AGGREGATE_KIND = "Vehicle"
PAYLOAD_SCHEMA = {
    "vehicle_id": {"type": "string", "required": True, "description": "车辆"},
    "station_id": {"type": "string", "required": True, "description": "收费站"},
    "speed": {"type": "number", "required": False, "description": "速度"},
}
PAYLOAD = {"vehicle_id": "V001", "station_id": "ST001", "speed": 32.5}


def _create(platform, wid, *, name=EVENT_TYPE, version=1, description="车辆驶入"):
    return CreateEventContract(platform.ctx)(
        wid, "admin", name, AGGREGATE_KIND, PAYLOAD_SCHEMA,
        description=description, version=version,
    )


def _published(platform, wid, *, name=EVENT_TYPE, version=1):
    _create(platform, wid, name=name, version=version)
    return PublishEventContract(platform.ctx)(wid, "admin", name)


def _subscribe(platform, wid, *, name="billing", event_type=EVENT_TYPE,
               filter=None, handler_key="billing_handler", enabled=True):
    return SubscribeEvent(platform.ctx)(
        wid, "admin", name, event_type,
        filter=filter, handler_key=handler_key, enabled=enabled,
    )


def _emit(platform, wid, *, event_type=EVENT_TYPE, aggregate_kind=AGGREGATE_KIND,
          aggregate_id="Vehicle/V001", payload=None):
    return EmitEvent(platform.ctx)(
        wid, "admin", event_type, aggregate_kind, aggregate_id,
        payload=PAYLOAD if payload is None else payload,
    )


def _audit(platform, wid, action):
    return [entry for entry in platform.ctx.audit.list(wid, limit=1000)
            if entry.get("action") == action]


# ----------------------------------------------------------------------
# contracts
# ----------------------------------------------------------------------

def test_contract_create_publish_and_list(platform, workspace_id):
    created = _create(platform, workspace_id)
    assert created["name"] == EVENT_TYPE
    assert created["id"] == EVENT_TYPE          # the name is the document id
    assert created["workspace_id"] == workspace_id
    assert created["lifecycle"] == "draft"
    assert created["version"] == 1
    assert created["aggregate_kind"] == AGGREGATE_KIND
    assert created["created_at"]
    assert created["payload_schema"]["station_id"]["required"] is True
    assert platform.ctx.repo.get("event_contract", workspace_id, EVENT_TYPE)
    assert _audit(platform, workspace_id, "event_contract.create")

    listed = ListEventContracts(platform.ctx)(workspace_id, "admin")
    assert [row["name"] for row in listed] == [EVENT_TYPE]
    assert ListEventContracts(platform.ctx)(workspace_id, "admin",
                                            lifecycle="published") == []

    published = PublishEventContract(platform.ctx)(workspace_id, "admin", EVENT_TYPE)
    assert published["lifecycle"] == "published"
    assert ListEventContracts(platform.ctx)(workspace_id, "admin",
                                            lifecycle="published") == [published]
    assert _audit(platform, workspace_id, "event_contract.publish")


def test_duplicate_creation_and_repeated_publish_are_rejected(platform, workspace_id):
    _create(platform, workspace_id)
    with pytest.raises(ValidationError, match="already exists"):
        _create(platform, workspace_id)
    PublishEventContract(platform.ctx)(workspace_id, "admin", EVENT_TYPE)
    with pytest.raises(ValidationError, match="already published"):
        PublishEventContract(platform.ctx)(workspace_id, "admin", EVENT_TYPE)
    # the stored contract is still the single published one
    rows = platform.ctx.repo.list("event_contract", workspace_id)
    assert len(rows) == 1 and rows[0]["lifecycle"] == "published"


def test_contract_definition_is_validated(platform, workspace_id):
    use_case = CreateEventContract(platform.ctx)
    with pytest.raises(ValidationError, match="invalid event name"):
        use_case(workspace_id, "admin", "Traffic.VehicleEntered", "Vehicle",
                 PAYLOAD_SCHEMA)
    with pytest.raises(ValidationError, match="payload"):
        use_case(workspace_id, "admin", "traffic.bad_schema", "Vehicle",
                 {"vehicle_id": {"type": "text"}})
    with pytest.raises(ValidationError, match="aggregate_kind"):
        use_case(workspace_id, "admin", "traffic.no_aggregate", "",
                 PAYLOAD_SCHEMA)
    with pytest.raises(ValidationError, match="version"):
        use_case(workspace_id, "admin", "traffic.bad_version", "Vehicle",
                 PAYLOAD_SCHEMA, version=0)
    assert platform.ctx.repo.list("event_contract", workspace_id) == []


def test_unknown_contract_is_not_found(platform, workspace_id):
    with pytest.raises(NotFoundError):
        PublishEventContract(platform.ctx)(workspace_id, "admin",
                                           "traffic.never_created")
    with pytest.raises(NotFoundError):
        _emit(platform, workspace_id, event_type="traffic.never_created")


# ----------------------------------------------------------------------
# subscriptions
# ----------------------------------------------------------------------

def test_subscribe_requires_a_published_contract_and_known_filter_fields(
        platform, workspace_id):
    _create(platform, workspace_id)
    with pytest.raises(ValidationError, match="publish"):
        _subscribe(platform, workspace_id)
    PublishEventContract(platform.ctx)(workspace_id, "admin", EVENT_TYPE)
    with pytest.raises(ValidationError, match="filter fields"):
        _subscribe(platform, workspace_id, filter={"ghost": 1})
    with pytest.raises(ValidationError, match="filter"):
        _subscribe(platform, workspace_id, filter=["station_id"])
    with pytest.raises(ValidationError, match="handler_key"):
        _subscribe(platform, workspace_id, handler_key="")
    with pytest.raises(ValidationError, match="invalid event name"):
        _subscribe(platform, workspace_id, event_type="not a name")
    with pytest.raises(ValidationError, match="name"):
        _subscribe(platform, workspace_id, name="")
    assert platform.ctx.repo.list("event_subscription", workspace_id) == []


def test_subscription_create_list_delete_and_audit(platform, workspace_id):
    _published(platform, workspace_id)
    subscription = _subscribe(platform, workspace_id,
                              filter={"station_id": "ST001"})
    assert subscription["id"]
    assert subscription["workspace_id"] == workspace_id
    assert subscription["handler_key"] == "billing_handler"
    assert subscription["filter"] == {"station_id": "ST001"}
    assert subscription["enabled"] is True
    assert subscription["created_at"]

    listed = ListEventSubscriptions(platform.ctx)(workspace_id, "admin")
    assert [row["id"] for row in listed] == [subscription["id"]]
    assert ListEventSubscriptions(platform.ctx)(workspace_id, "admin",
                                                event_type=EVENT_TYPE)
    assert _audit(platform, workspace_id, "event_subscription.create")

    result = DeleteEventSubscription(platform.ctx)(
        workspace_id, "admin", subscription["id"])
    assert result == {"deleted": True, "id": subscription["id"]}
    assert platform.ctx.repo.get("event_subscription", workspace_id,
                                 subscription["id"]) is None
    assert ListEventSubscriptions(platform.ctx)(workspace_id, "admin") == []
    assert _audit(platform, workspace_id, "event_subscription.delete")
    with pytest.raises(NotFoundError):
        DeleteEventSubscription(platform.ctx)(workspace_id, "admin",
                                              subscription["id"])


# ----------------------------------------------------------------------
# EmitEvent
# ----------------------------------------------------------------------

def test_emit_event_stamps_the_contract_and_matches_subscriptions(
        platform, workspace_id):
    _published(platform, workspace_id, version=2)
    subscription = _subscribe(platform, workspace_id)
    event = _emit(platform, workspace_id)

    assert event["contract"] == EVENT_TYPE
    assert event["event_version"] == 2
    assert event["type"] == EVENT_TYPE
    assert event["payload"] == PAYLOAD
    assert event["aggregate"] == {"kind": AGGREGATE_KIND, "id": "Vehicle/V001",
                                  "version": None}
    assert event["dispatched_at"] is None
    assert [sub["id"] for sub in event["matched_subscriptions"]] == [
        subscription["id"]]
    assert event["matched_subscriptions"][0]["handler_key"] == "billing_handler"

    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["contract"] == EVENT_TYPE and stored["payload"] == PAYLOAD
    assert platform.events.list(workspace_id, event_type=EVENT_TYPE)

    emitted = _audit(platform, workspace_id, "event.emit")
    assert emitted, "emitting a business event must be audited"
    detail = emitted[-1]["detail"]
    assert detail["event_id"] == event["id"]
    assert detail["type"] == EVENT_TYPE
    assert "payload" not in detail and "payload" not in str(detail)


def test_emit_event_rejects_invalid_payloads_and_aggregates(platform, workspace_id):
    _published(platform, workspace_id)
    invalid_payloads = [
        ({"vehicle_id": "V001"}, "missing required field"),           # station missing
        ({**PAYLOAD, "ghost": 1}, "unknown field"),
        ({**PAYLOAD, "speed": "fast"}, "must be of type"),
        (["not-an-object"], "payload must be an object"),
    ]
    for payload, expected in invalid_payloads:
        with pytest.raises(ValidationError, match=expected):
            _emit(platform, workspace_id, payload=payload)
    with pytest.raises(ValidationError, match="aggregate_kind"):
        _emit(platform, workspace_id, aggregate_kind="TollStation")
    with pytest.raises(ValidationError, match="aggregate_id"):
        _emit(platform, workspace_id, aggregate_id="")
    assert not [row for row in platform.ctx.repo.list("event", workspace_id)
                if row.get("type") == EVENT_TYPE]


def test_emit_event_requires_a_published_contract(platform, workspace_id):
    _create(platform, workspace_id)
    with pytest.raises(ValidationError, match="publish"):
        _emit(platform, workspace_id)


def test_event_log_contract_path_validates_envelope(platform, workspace_id):
    _published(platform, workspace_id)
    stored = platform.ctx.repo.get("event_contract", workspace_id, EVENT_TYPE)
    published = EventContract.from_dict(stored)
    with pytest.raises(ValidationError, match="does not match event contract"):
        platform.events.emit("traffic.other_event", workspace_id, "admin",
                             AGGREGATE_KIND, "Vehicle/V001", {},
                             contract=published)
    draft = EventContract.from_dict(stored)
    draft.lifecycle = "draft"
    with pytest.raises(ValidationError, match="published"):
        platform.events.emit(EVENT_TYPE, workspace_id, "admin", AGGREGATE_KIND,
                             "Vehicle/V001", PAYLOAD, contract=draft)
    with pytest.raises(ValidationError, match="payload must be an object"):
        platform.events.emit(EVENT_TYPE, workspace_id, "admin", AGGREGATE_KIND,
                             "Vehicle/V001", [], contract=published)
    event = platform.events.emit(EVENT_TYPE, workspace_id, "admin", AGGREGATE_KIND,
                                 "Vehicle/V001", PAYLOAD, contract=published)
    assert event["event_version"] == published.version
    assert event["contract"] == EVENT_TYPE


# ----------------------------------------------------------------------
# Dispatcher delivery through subscription.handler_key
# ----------------------------------------------------------------------

def test_dispatcher_delivers_to_the_registered_handler(platform, workspace_id):
    _published(platform, workspace_id)
    subscription = _subscribe(platform, workspace_id,
                             filter={"station_id": "ST001"})
    received: list[dict] = []

    def handler(event: dict) -> None:
        received.append(event)

    platform.dispatcher.subscribe_key("billing_handler", handler)
    event = _emit(platform, workspace_id)
    result = platform.dispatcher.dispatch(workspace_id)

    assert result["dispatched"] >= 1 and result["failed"] == 0
    assert [item["id"] for item in received] == [event["id"]]
    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["dispatched_at"] is not None
    assert stored["delivery"] == "delivered"
    assert "billing:billing_handler" in stored["consumer"]
    assert subscription["name"] in stored["consumer"]

    delivered_at = stored["dispatched_at"]
    platform.dispatcher.dispatch(workspace_id)
    again = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert again["dispatched_at"] == delivered_at
    assert len(received) == 1, "a delivered event is not re-delivered"


def test_dispatcher_defers_when_handler_key_is_not_registered(platform, workspace_id):
    _published(platform, workspace_id)
    _subscribe(platform, workspace_id, handler_key="late_handler")
    event = _emit(platform, workspace_id)

    result = platform.dispatcher.dispatch(workspace_id)
    assert result["skipped"] >= 1
    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["dispatched_at"] is None
    assert "late_handler" in (stored.get("last_error") or "")

    received: list[dict] = []
    platform.dispatcher.subscribe_key("late_handler", received.append)
    result = platform.dispatcher.dispatch(workspace_id)
    assert result["dispatched"] >= 1
    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["delivery"] == "delivered"
    assert [item["id"] for item in received] == [event["id"]]
    assert stored["attempts"] == 0, "a deferred event is not a failed attempt"


def test_dispatcher_does_not_deliver_filter_mismatches(platform, workspace_id):
    _published(platform, workspace_id)
    _subscribe(platform, workspace_id, filter={"station_id": "ST001"})
    calls: list[dict] = []
    platform.dispatcher.subscribe_key("billing_handler", calls.append)
    event = _emit(platform, workspace_id, payload={**PAYLOAD, "station_id": "ST002"})
    assert event["matched_subscriptions"] == []

    platform.dispatcher.dispatch(workspace_id)
    assert calls == []
    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["dispatched_at"] is not None
    assert stored["delivery"] == "no_subscribers"


def test_dispatcher_skips_disabled_subscriptions(platform, workspace_id):
    _published(platform, workspace_id)
    _subscribe(platform, workspace_id, enabled=False)
    calls: list[dict] = []
    platform.dispatcher.subscribe_key("billing_handler", calls.append)
    event = _emit(platform, workspace_id)
    assert event["matched_subscriptions"] == []
    platform.dispatcher.dispatch(workspace_id)
    assert calls == []
    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["delivery"] == "no_subscribers"


def test_dispatcher_filter_list_membership_delivers(platform, workspace_id):
    _published(platform, workspace_id)
    _subscribe(platform, workspace_id,
               filter={"station_id": ["ST001", "ST002"]})
    received: list[dict] = []
    platform.dispatcher.subscribe_key("billing_handler", received.append)
    event = _emit(platform, workspace_id, payload={**PAYLOAD, "station_id": "ST002"})
    assert len(event["matched_subscriptions"]) == 1
    platform.dispatcher.dispatch(workspace_id)
    assert [item["id"] for item in received] == [event["id"]]


# ----------------------------------------------------------------------
# permissions + lifecycle-event regression
# ----------------------------------------------------------------------

def test_viewers_cannot_define_or_emit_events(platform, workspace_id):
    platform.workspaces.add_member(workspace_id, "reader", "viewer", "admin")
    with pytest.raises(AccessDeniedError):
        CreateEventContract(platform.ctx)(
            workspace_id, "reader", EVENT_TYPE, AGGREGATE_KIND, PAYLOAD_SCHEMA)
    with pytest.raises(AccessDeniedError):
        PublishEventContract(platform.ctx)(workspace_id, "reader", EVENT_TYPE)
    with pytest.raises(AccessDeniedError):
        EmitEvent(platform.ctx)(
            workspace_id, "reader", EVENT_TYPE, AGGREGATE_KIND, "Vehicle/V001",
            payload=PAYLOAD)
    # viewers may still read (list) contracts
    assert ListEventContracts(platform.ctx)(workspace_id, "reader") == []


def test_legacy_catalog_events_and_dispatcher_behaviour_are_unchanged(
        platform, workspace_id):
    event = platform.events.emit("release.cut", workspace_id, "admin",
                                 "release", "rel_demo", payload={})
    assert event["event_version"] == 1
    assert "contract" not in event
    result = platform.dispatcher.dispatch(workspace_id)
    assert result["dispatched"] == 0 and result["skipped"] >= 1
    stored = platform.ctx.repo.get("event", workspace_id, event["id"])
    assert stored["delivery"] == "no_subscribers"
    assert stored["dispatched_at"] is not None
    with pytest.raises(ValidationError):
        platform.events.emit("nope.happened", workspace_id, "admin", "x", "1")
