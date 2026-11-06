"""EventContract / EventSubscription domain contract (P1-Event).

Pure domain tests: naming, schema validation, payload validation, filter
matching and dict round-trips.  No platform, no repository.
"""

from __future__ import annotations

import pytest

from onto_platform.domain.event import (
    EventContract,
    EventSubscription,
    matching_subscriptions,
    validate_event_name,
)
from onto_platform.errors import ValidationError

SCHEMA = {
    "vehicle_id": {"type": "string", "required": True, "description": "车辆 ID"},
    "station_id": {"type": "string", "required": True, "description": "收费站 ID"},
    "speed": {"type": "number", "required": False, "description": "速度"},
    "passengers": {"type": "integer", "required": False, "description": "人数"},
    "paid": {"type": "boolean", "required": False, "description": "是否已支付"},
    "context": {"type": "object", "required": False, "description": "扩展上下文"},
    "tags": {"type": "array", "required": False, "description": "标签"},
}


def _contract(**overrides) -> EventContract:
    values = {
        "name": "traffic.vehicle_entered",
        "version": 1,
        "description": "车辆驶入收费站",
        "aggregate_kind": "Vehicle",
        "payload_schema": SCHEMA,
    }
    values.update(overrides)
    return EventContract(**values)


def _event(payload: dict | None = None, event_type: str = "traffic.vehicle_entered",
           enabled: bool | None = None) -> dict:
    event = {"type": event_type, "payload": payload or {}}
    if enabled is not None:
        event["enabled"] = enabled
    return event


# ----------------------------------------------------------------------
# naming
# ----------------------------------------------------------------------

def test_valid_event_names_are_accepted():
    for name in ("traffic.vehicle_entered", "a.b", "a1.b2_c", "traffic.toll_paid"):
        assert validate_event_name(name) == name


@pytest.mark.parametrize("name", [
    "",
    "traffic",
    "Traffic.vehicle",
    "traffic.Vehicle",
    "traffic.",
    ".traffic",
    "1traffic.vehicle",
    "traffic..vehicle",
    "traffic.vehicle-entered",
    "traffic vehicle.entered",
])
def test_invalid_event_names_are_rejected(name):
    with pytest.raises(ValidationError):
        validate_event_name(name)
    with pytest.raises(ValidationError):
        _contract(name=name)


def test_contract_round_trip():
    contract = _contract(lifecycle="published", created_at="2026-01-01T00:00:00",
                         updated_at="2026-01-02T00:00:00")
    restored = EventContract.from_dict(contract.to_dict())
    assert restored == contract
    assert restored.payload_schema["vehicle_id"] == {
        "type": "string", "required": True, "description": "车辆 ID",
    }
    # to_dict is a copy: mutating it does not change the domain object
    restored.to_dict()["payload_schema"]["vehicle_id"]["type"] = "number"
    assert restored.payload_schema["vehicle_id"]["type"] == "string"


@pytest.mark.parametrize("version", [0, -1, 1.5, "1", True])
def test_contract_rejects_invalid_versions(version):
    with pytest.raises(ValidationError):
        _contract(version=version)


def test_contract_requires_aggregate_kind():
    with pytest.raises(ValidationError):
        _contract(aggregate_kind="   ")


@pytest.mark.parametrize("schema", [
    [],
    "vehicle_id",
    {"vehicle_id": "string"},
    {"vehicle_id": {"description": "no type"}},
    {"vehicle_id": {"type": "text"}},
    {"vehicle_id": {"type": "string", "required": "yes"}},
    {"vehicle_id": {"type": "string", "description": 3}},
    {"": {"type": "string"}},
])
def test_contract_rejects_invalid_payload_schemas(schema):
    with pytest.raises(ValidationError):
        _contract(payload_schema=schema)


def test_contract_keeps_only_normalized_spec_keys():
    contract = _contract(payload_schema={
        "vehicle_id": {"type": "string", "required": True,
                       "description": "车辆", "extra": "dropped"},
    })
    assert contract.payload_schema == {
        "vehicle_id": {"type": "string", "required": True, "description": "车辆"},
    }


def test_contract_from_dict_rejects_malformed_document():
    with pytest.raises(ValidationError):
        EventContract.from_dict({"version": 1})
    with pytest.raises(ValidationError):
        EventContract.from_dict("not-a-dict")


# ----------------------------------------------------------------------
# payload validation
# ----------------------------------------------------------------------

def test_payload_validation_accepts_all_base_types():
    payload = {
        "vehicle_id": "V001",
        "station_id": "ST001",
        "speed": 42.5,
        "passengers": 3,
        "paid": False,
        "context": {"lane": 2},
        "tags": ["vip", "etc"],
    }
    assert _contract().validate_payload(payload) == []


def test_payload_validation_reports_missing_required_fields():
    problems = _contract().validate_payload({"vehicle_id": "V001"})
    assert problems == ["missing required field: station_id"]


def test_payload_validation_reports_unknown_fields():
    problems = _contract().validate_payload({
        "vehicle_id": "V001", "station_id": "ST001", "ghost": 1,
    })
    assert problems == ["unknown field: ghost"]


@pytest.mark.parametrize("field_name,value,expected_type", [
    ("vehicle_id", 1, "string"),
    ("speed", True, "number"),          # bool is not a number
    ("passengers", 1.5, "integer"),     # float is not an integer
    ("passengers", True, "integer"),
    ("paid", 1, "boolean"),
    ("context", [], "object"),
    ("tags", {}, "array"),
    ("speed", None, "number"),
])
def test_payload_validation_reports_type_mismatches(field_name, value, expected_type):
    payload = {"vehicle_id": "V001", "station_id": "ST001"}
    payload[field_name] = value
    problems = _contract().validate_payload(payload)
    assert any(expected_type in problem and field_name in problem
               for problem in problems), problems


def test_payload_validation_rejects_non_object_payload():
    assert _contract().validate_payload(["nope"]) == ["payload must be an object"]


# ----------------------------------------------------------------------
# subscriptions
# ----------------------------------------------------------------------

def _subscription(**overrides) -> EventSubscription:
    values = {
        "id": "sub_1",
        "workspace_id": "ws_1",
        "name": "billing",
        "event_type": "traffic.vehicle_entered",
        "filter": {},
        "handler_key": "billing_handler",
        "enabled": True,
        "created_at": "2026-01-01T00:00:00",
    }
    values.update(overrides)
    return EventSubscription(**values)


def test_subscription_round_trip():
    subscription = _subscription(filter={"station_id": ["ST001", "ST002"]})
    restored = EventSubscription.from_dict(subscription.to_dict())
    assert restored == subscription
    assert restored.filter == {"station_id": ["ST001", "ST002"]}


def test_subscription_rejects_malformed_documents():
    with pytest.raises(ValidationError):
        _subscription(handler_key="")
    with pytest.raises(ValidationError):
        _subscription(event_type="not a name")
    with pytest.raises(ValidationError):
        _subscription(filter=[])
    with pytest.raises(ValidationError):
        _subscription(filter={"station_id": []})
    with pytest.raises(ValidationError):
        _subscription(filter={"station_id": {"value": "ST001"}})
    with pytest.raises(ValidationError):
        _subscription(enabled="yes")


def test_empty_filter_matches_every_payload():
    subscription = _subscription()
    assert subscription.matches(_event())
    assert subscription.matches(_event({"vehicle_id": "V001"}))
    assert subscription.matches({"payload": None})
    assert subscription.matches({})


def test_filter_matches_by_equality():
    subscription = _subscription(filter={"station_id": "ST001", "paid": True})
    assert subscription.matches(_event(
        {"station_id": "ST001", "paid": True, "vehicle_id": "V001"}))
    assert not subscription.matches(_event({"station_id": "ST002", "paid": True}))
    assert not subscription.matches(_event({"station_id": "ST001", "paid": False}))
    # a missing payload field never equals a concrete expectation
    assert not subscription.matches(_event({"paid": True}))


def test_filter_list_means_value_in_list():
    subscription = _subscription(filter={"station_id": ["ST001", "ST002"]})
    assert subscription.matches(_event({"station_id": "ST002"}))
    assert not subscription.matches(_event({"station_id": "ST003"}))
    assert not subscription.matches(_event({}))


def test_matching_subscriptions_applies_enabled_and_event_type():
    billing = _subscription(name="billing", filter={"station_id": "ST001"})
    disabled = _subscription(id="sub_2", name="disabled",
                             filter={"station_id": "ST001"}, enabled=False)
    other_type = _subscription(id="sub_3", name="other",
                               event_type="traffic.toll_paid",
                               filter={"station_id": "ST001"})
    event = _event({"station_id": "ST001"})
    matched = matching_subscriptions([billing, disabled, other_type], event)
    assert [subscription.name for subscription in matched] == ["billing"]
    # matching does not mutate or consume the subscription's filters
    assert billing.filter == {"station_id": "ST001"}
