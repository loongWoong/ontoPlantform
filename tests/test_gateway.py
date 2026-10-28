"""KernelRuntimeGateway: the Platform <-> Kernel boundary (§30/§31)."""

from __future__ import annotations

import pytest

from onto_platform.errors import RuntimeUnavailableError, ValidationError
from onto_platform.infrastructure.runtime.kernel_gateway import KernelRuntimeGateway
from onto_platform.ports.runtime import (
    LinkAssert,
    ObjectUpsert,
    SchemaPayload,
    SemanticDelta,
    UnlinkAssert,
)

SCHEMA = {
    "ontology": {"name": "traffic", "version": 1},
    "objects": {
        "Vehicle": {
            "description": "车辆",
            "properties": {"plate": {"type": "string"},
                           "balance": {"type": "number"}},
        },
        "TollStation": {
            "description": "收费站",
            "properties": {"name": {"type": "string"}},
        },
    },
    "relations": {"passes": {"source": "Vehicle", "target": "TollStation"}},
    "functions": {},
}


def make_gateway() -> KernelRuntimeGateway:
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(schema=SCHEMA))
    return gateway


def test_requires_published_schema():
    gateway = KernelRuntimeGateway()
    with pytest.raises(RuntimeUnavailableError):
        gateway.describe()
    result = gateway.invoke("Vehicle.recharge")
    assert result.ok is False
    assert "publish" in result.error


def test_accepts_legacy_snake_case_cardinality():
    """Stored payloads from older versions / LLM drafts used one_to_many.

    The gateway must normalize them on the way in so previously published
    workspaces keep loading after a kernel upgrade, without rewriting the
    stored document.
    """
    schema = {
        "ontology": {"name": "legacy", "version": 1},
        "objects": {
            "TollStation": {"properties": {"name": {"type": "string"}}},
            "Lane": {"properties": {"code": {"type": "string"}}},
        },
        "relations": {"has_lane": {"source": "TollStation", "target": "Lane",
                                   "cardinality": "one_to_many"}},
        "functions": {},
    }
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(schema=schema))
    relations = {r["name"]: r for r in gateway.describe()["relations"]}
    assert relations["has_lane"]["cardinality"] == "one-to-many"
    # the caller's payload is not mutated: the repository keeps its bytes
    assert schema["relations"]["has_lane"]["cardinality"] == "one_to_many"


def test_describe_and_reads():
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001",
                              properties={"plate": "鲁A", "balance": 10}),
                 ObjectUpsert(type="TollStation", id="ST001",
                              properties={"name": "济南西"})),
        links=(LinkAssert(relation="passes", source="Vehicle/V001",
                          target="TollStation/ST001"),),
    ))
    index = gateway.describe()
    assert {o["name"] for o in index["objects"]} == {"Vehicle", "TollStation"}
    assert gateway.get_object("Vehicle/V001")["properties"]["balance"] == 10
    assert gateway.get_object("Vehicle/NOPE") is None
    assert gateway.find_objects("Vehicle", {"balance__gt": 5})
    assert not gateway.find_objects("Vehicle", {"balance__gt": 50})
    assert gateway.related("Vehicle/V001", "passes") == ["TollStation/ST001"]
    assert gateway.related("TollStation/ST001", "passes", direction="in") == ["Vehicle/V001"]


def test_apply_delta_creates_updates_and_validates():
    gateway = make_gateway()
    changes = gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001",
                              properties={"plate": "鲁A", "balance": 10}),),
    ))
    assert changes[0]["kind"] == "create_object"

    # update: only changed properties produce changes
    changes = gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001",
                              properties={"balance": 30}),),
    ))
    assert changes == [{"kind": "set_property", "object": "Vehicle/V001",
                        "property": "balance", "old": 10, "new": 30}]

    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Alien", id="X1"),),))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="X1", properties={"balance": "NaN"}),),))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="X1", properties={"ghost": 1}),),))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(links=(
            LinkAssert(relation="passes", source="TollStation/ST001",
                       target="Vehicle/V001"),),))  # reversed endpoints


def test_unlink_and_idempotent_links():
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001"),
                 ObjectUpsert(type="TollStation", id="ST001")),
        links=(LinkAssert(relation="passes", source="Vehicle/V001",
                          target="TollStation/ST001"),),
    ))
    gateway.apply_delta(SemanticDelta(
        links=(LinkAssert(relation="passes", source="Vehicle/V001",
                          target="TollStation/ST001"),),
    ))
    assert len(gateway.related("Vehicle/V001", "passes")) == 1
    gateway.apply_delta(SemanticDelta(
        unlinks=(UnlinkAssert(relation="passes", source="Vehicle/V001",
                              target="TollStation/ST001"),),
    ))
    assert gateway.related("Vehicle/V001", "passes") == []


def test_state_survives_republish():
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001", properties={"balance": 7}),),
    ))
    schema_v2 = dict(SCHEMA)
    schema_v2["ontology"] = {"name": "traffic", "version": 2}
    gateway.publish_schema(SchemaPayload(schema=schema_v2))
    assert gateway.get_object("Vehicle/V001")["properties"]["balance"] == 7
    assert gateway.describe()["ontology"]["version"] == 2


def test_invoke_never_raises_and_logs_changes():
    gateway = make_gateway()
    gateway.publish_schema(SchemaPayload(
        schema={**SCHEMA,
                "functions": {"Vehicle.recharge": {
                    "target": "Vehicle", "description": "充值",
                    "input": {"amount": {"type": "number"}}}}},
        bindings={"Vehicle.recharge": {"executor": "python",
                                       "entrypoint": "missing.py"}},
    ))
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001", properties={"balance": 1}),),
    ))
    result = gateway.invoke("Vehicle.recharge", target="Vehicle/V001", args={"amount": 5})
    assert result.ok is False  # binding file missing -> error result, not raise
    result = gateway.invoke("Nope.nothing")
    assert result.ok is False
    result = gateway.invoke("Vehicle.recharge", target="Vehicle/V001", args={"amount": "x"})
    assert result.ok is False


def test_change_log_sequence():
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V001", properties={"balance": 1}),),
    ))
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V002", properties={"balance": 2}),),
    ))
    first = gateway.observe_changes(after_seq=0)
    assert [e["seq"] for e in first] == [1, 2]
    assert gateway.observe_changes(after_seq=1)[0]["seq"] == 2
    assert gateway.observe_changes(after_seq=2) == []


def test_invalid_ref_format():
    gateway = make_gateway()
    with pytest.raises(ValidationError):
        gateway.get_object("VehicleV001")
    result = gateway.invoke("f", target="bad-ref")
    assert result.ok is False
