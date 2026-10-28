"""0.3/0.9 platform-side constraint execution through the real kernel.

These tests exercise the platform->kernel boundary end to end: compiled
facets/identity/cardinality must be enforced by the kernel at write time,
and a rejected delta must never leave partial state behind.
"""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError
from onto_platform.infrastructure.runtime.kernel_gateway import KernelRuntimeGateway
from onto_platform.ports.runtime import (
    LinkAssert,
    ObjectUpsert,
    SchemaPayload,
    SemanticDelta,
)

SCHEMA = {
    "ontology": {"name": "traffic", "version": 1, "profile": "closed"},
    "objects": {
        "Vehicle": {
            "identity": ["plate"],
            "properties": {
                "plate": {"type": "string", "required": True, "unique": True},
                "balance": {"type": "number", "minimum": 0},
                "status": {"type": "string", "enum": ["free", "congested"]},
            },
        },
        "Lane": {"properties": {"name": {"type": "string"}}},
    },
    "relations": {
        "occupies": {"source": "Vehicle", "target": "Lane", "cardinality": "one-to-one"},
    },
    "functions": {},
}


def make_gateway() -> KernelRuntimeGateway:
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(schema=SCHEMA))
    return gateway


def test_delta_is_single_transaction():
    """One bad upsert rolls back the whole delta (no ghost objects)."""
    gateway = make_gateway()
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V1", properties={"plate": "鲁A"}),
            ObjectUpsert(type="Vehicle", id="V2",
                         properties={"plate": 5}),  # wrong scalar type
        )))
    assert gateway.get_object("Vehicle/V1") is None
    assert gateway.get_object("Vehicle/V2") is None


def test_enum_and_range_rejected_at_write_time():
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "鲁A", "status": "free"}),)))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V1",
                         properties={"status": "flying"}),)))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V1",
                         properties={"balance": -1}),)))
    assert gateway.get_object("Vehicle/V1")["properties"]["status"] == "free"


def test_identity_collision_detected_across_sources():
    """Two source ids carrying the same business identity are rejected."""
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1", properties={"plate": "鲁A"}),)))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V2",
                         properties={"plate": "鲁A"}),)))
    assert gateway.get_object("Vehicle/V2") is None


def test_cardinality_executed():
    gateway = make_gateway()
    gateway.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V1",
                              properties={"plate": "鲁A"}),
                 ObjectUpsert(type="Vehicle", id="V2",
                              properties={"plate": "鲁B"}),
                 ObjectUpsert(type="Lane", id="L1",
                              properties={"name": "lane"}),
                 ObjectUpsert(type="Lane", id="L2",
                              properties={"name": "lane2"})),
        links=(LinkAssert(relation="occupies", source="Vehicle/V1",
                          target="Lane/L1"),),
    ))
    # V2 -> L1 is a second edge into the 1:1 target: rejected
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(links=(
            LinkAssert(relation="occupies", source="Vehicle/V2",
                       target="Lane/L1"),)))
    # V1 -> L2 is a second outgoing edge from the 1:1 source: rejected
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(links=(
            LinkAssert(relation="occupies", source="Vehicle/V1",
                       target="Lane/L2"),)))
    assert gateway.related("Lane/L1", "occupies", direction="in") == ["Vehicle/V1"]


def test_set_property_missing_target_rejected():
    gateway = make_gateway()
    with pytest.raises(ValidationError):
        gateway.apply_delta(
            SemanticDelta(upserts=(
                ObjectUpsert(type="Vehicle", id="NOPE",
                             properties={"balance": 1}),)),
            source="invoke", must_exist=True)
    assert gateway.get_object("Vehicle/NOPE") is None


def _publish_constrained_workspace(platform) -> str:
    ws = platform.workspaces.create("constraints", actor="admin")
    wid = ws["id"]
    platform.ontologies.create(wid, "traffic", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[
            {"name": "plate", "type": "string", "required": True, "unique": True},
            {"name": "status", "type": "string", "enum": ["free", "congested"]},
        ],
        identity=["plate"])
    platform.ontologies.upsert_object(
        wid, "admin", "Lane", properties=[{"name": "name", "type": "string"}])
    platform.ontologies.upsert_relation(
        wid, "admin", "occupies", "Vehicle", "Lane", cardinality="one-to-one")
    platform.ontologies.publish(wid, "admin")
    return wid


def test_projection_enum_violation_rejected(platform):
    wid = _publish_constrained_workspace(platform)
    source = platform.data.connect_source(
        wid, "admin", "vehicles", "static",
        assets=[{"name": "vehicles", "config": {"rows": [
            {"plate": "鲁A", "status": "flying"},
        ]}}])
    projection = platform.data.create_projection(
        wid, "admin", "p", source["id"],
        object_mappings=[{
            "asset": "vehicles", "object_type": "Vehicle", "id_field": "plate",
            "fields": [{"source_field": "plate", "property": "plate"},
                       {"source_field": "status", "property": "status"}],
        }])
    with pytest.raises(ValidationError):
        platform.data.run_projection(wid, projection["id"], "admin")


def test_projection_identity_collision_detected(platform):
    """Two source rows with different ids but the same identity are rejected.

    The projection id field must equal the declared identity, so this is
    driven through the gateway directly - exactly what a second source
    carrying a conflicting identity would produce.
    """
    wid = _publish_constrained_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "鲁A", "status": "free"}),)))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V2",
                         properties={"plate": "鲁A"}),)))


# ----------------------------------------------------------------------
# 0.5 state temporalization surfaced through the gateway
# ----------------------------------------------------------------------


def test_object_exposes_version_updated_at_source(platform):
    wid = _publish_constrained_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "鲁A", "status": "free"}),)))
    obj = gateway.get_object("Vehicle/V1")
    assert obj["version"] >= 1
    assert obj["updated_at"]
    assert obj["source"] == "projection"
    # a second write bumps the version and re-labels the writer
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1", properties={"status": "congested"}),)),
        source="workflow")
    updated = gateway.get_object("Vehicle/V1")
    assert updated["version"] == obj["version"] + 1
    assert updated["source"] == "workflow"


def test_change_entries_carry_seq_ts_source(platform):
    wid = _publish_constrained_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "鲁A", "status": "free"}),)))
    entries = gateway.observe_changes(after_seq=0)
    assert entries
    first = entries[0]
    assert {"seq", "ts", "source", "changes"} <= set(first)
    assert first["source"] == "projection"
    assert [e["seq"] for e in entries] == sorted(e["seq"] for e in entries)


# ----------------------------------------------------------------------
# 0.3 open-world find report (O-9: unknown is not false)
# ----------------------------------------------------------------------


def test_find_reports_excluded_missing(platform):
    wid = _publish_constrained_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "鲁A", "status": "free"}),
        ObjectUpsert(type="Vehicle", id="V2", properties={"plate": "鲁B"}),
    )))
    report = gateway.find_objects("Vehicle", {"status": "free"}, report=True)
    assert [o["id"] for o in report["objects"]] == ["V1"]
    assert report["excluded_missing"] == 1
    plain = gateway.find_objects("Vehicle", {"status": "free"})
    assert isinstance(plain, list) and [o["id"] for o in plain] == ["V1"]


# ----------------------------------------------------------------------
# 0.7 lock scope: a slow capability must not block reads
# ----------------------------------------------------------------------

SLOW_SOURCE = '''
import time

def slow(ctx):
    time.sleep(0.4)
    ctx.set(ctx.target, "balance", 99)
    return {"done": True}
'''

SLOW_SCHEMA = {
    "ontology": {"name": "traffic", "version": 1, "profile": "closed"},
    "objects": {"Vehicle": {"properties": {
        "plate": {"type": "string"},
        "balance": {"type": "number"},
    }}},
    "relations": {},
    "functions": {"Vehicle.slow": {
        "target": "Vehicle", "description": "slow", "input": {},
    }},
}


def test_slow_invoke_does_not_block_reads(tmp_path):
    import threading
    import time

    source = tmp_path / "slow.py"
    source.write_text(SLOW_SOURCE, encoding="utf-8")
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(
        schema=SLOW_SCHEMA,
        bindings={"Vehicle.slow": {
            "executor": "python", "entrypoint": f"{source}:slow"}},
    ))
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "鲁A", "balance": 1}),)))

    results: list = []
    worker = threading.Thread(
        target=lambda: results.append(
            gateway.invoke("Vehicle.slow", target="Vehicle/V1", args={})),
    )
    worker.start()
    time.sleep(0.05)  # let the invoke enter its sleep
    started = time.monotonic()
    obj = gateway.get_object("Vehicle/V1")
    elapsed = time.monotonic() - started
    worker.join(timeout=10)
    assert elapsed < 0.25, f"read blocked for {elapsed:.2f}s behind a slow invoke"
    assert obj is not None and obj["properties"]["balance"] == 1
    assert results and results[0].ok
    assert gateway.get_object("Vehicle/V1")["properties"]["balance"] == 99
    entry = gateway.observe_changes(after_seq=0)[-1]
    assert entry["source"] == "invoke"
    assert entry["execution_id"].startswith("exec-")


DEAD_LOOP_SOURCE = '''
import time

def spin(ctx):
    while True:
        time.sleep(0.01)
'''


def test_dead_loop_times_out_as_unknown(tmp_path):
    source = tmp_path / "spin.py"
    source.write_text(DEAD_LOOP_SOURCE, encoding="utf-8")
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(
        schema={**SLOW_SCHEMA,
                "functions": {"Vehicle.spin": {
                    "target": "Vehicle", "description": "spin", "input": {}}}},
        bindings={"Vehicle.spin": {
            "executor": "python", "entrypoint": f"{source}:spin"}},
    ))
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1", properties={"plate": "鲁A"}),)))
    result = gateway.invoke("Vehicle.spin", target="Vehicle/V1", args={},
                            timeout=0.3)
    # a timeout is an unknown outcome, never a success
    assert result.status == "unknown"
    assert result.ok is False
    assert "unknown" in result.error
