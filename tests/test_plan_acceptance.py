"""Acceptance anchors named exactly as plan-v2 §0.1-1.8 require.

The plan uses these test names as the contract between the document and the
suite; keeping them here (in one place) makes the mapping auditable instead of
relying on prose.  Each test exercises the named behaviour end to end through
the same public seams real callers use.
"""

from __future__ import annotations

import copy

import pytest

from onto_platform.application.agent_runner import AgentRunner
from onto_platform.application.events import EventLog
from onto_platform.domain.governance.model import transition
from onto_platform.errors import AccessDeniedError, LifecycleError, ValidationError
from onto_platform.infrastructure.runtime.kernel_gateway import KernelRuntimeGateway
from onto_platform.ports.runtime import ObjectUpsert, SchemaPayload, SemanticDelta

from conftest import seed_workspace
from fakes import FakeProvider

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
    },
    "relations": {},
    "functions": {},
}

SLOW_SCHEMA = {
    "ontology": {"name": "traffic", "version": 1, "profile": "closed"},
    "objects": {"Vehicle": {"properties": {
        "plate": {"type": "string"}, "balance": {"type": "number"}}}},
    "relations": {},
    "functions": {"Vehicle.spin": {
        "target": "Vehicle", "description": "spin", "input": {}}},
}

DEAD_LOOP_SOURCE = '''
import time

def spin(ctx):
    while True:
        time.sleep(0.01)
'''

NOOP_SOURCE = '''
def execute(ctx):
    return {"success": True}
'''

CONTRACT = {
    "postconditions": [
        {"property": "balance", "op": "eq_prev_plus", "value": {"args": "amount"}},
    ],
    "effects": ["Vehicle.balance"],
    "idempotent": False,
}


def _gateway(schema: dict = SCHEMA) -> KernelRuntimeGateway:
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(schema=copy.deepcopy(schema)))
    return gateway


def _constrained_workspace(platform) -> str:
    wid = platform.workspaces.create("constraints", actor="admin")["id"]
    platform.ontologies.create(wid, "traffic", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string", "required": True,
                     "unique": True},
                    {"name": "status", "type": "string",
                     "enum": ["free", "congested"]}],
        identity=["plate"])
    platform.ontologies.publish(wid, "admin")
    return wid


def _publish_workflow(platform, wid, key, steps):
    capability = platform.capabilities.create(
        wid, "admin", key=key, name=key, target_type="Vehicle",
        implementation={"kind": "workflow", "steps": steps})
    platform.capabilities.publish(wid, capability["id"], "admin")


def _balance(platform, wid) -> float:
    return platform.ctx.gateway_for(wid).get_object(
        "Vehicle/V001")["properties"]["balance"]


# ----------------------------------------------------------------------
# 0.3 constraints
# ----------------------------------------------------------------------


def test_identity_declared_and_enforced():
    gateway = _gateway()
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1", properties={"plate": "A"}),)))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V2", properties={"plate": "A"}),)))


def test_cross_source_id_collision_detected():
    gateway = _gateway()
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1", properties={"plate": "A"}),)))
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V2", properties={"plate": "A"}),)))
    assert gateway.get_object("Vehicle/V2") is None


def test_property_enum_violation_rejected():
    gateway = _gateway()
    with pytest.raises(ValidationError):
        gateway.apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Vehicle", id="V1",
                         properties={"plate": "A", "status": "flying"}),)))


def test_publish_rejects_dangling_reference(platform):
    wid = platform.workspaces.create("dangling", actor="admin")["id"]
    platform.ontologies.create(wid, "dangling", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle", [{"name": "plate", "type": "string"}])
    # a relation added out of band (migration/import) must not publish while
    # it points at an undefined type
    document, version = platform.ctx.load_versioned(
        "ontology", wid, platform.ontologies.get(wid, "admin")["id"])
    document["relations"]["parks_at"] = {
        "name": "parks_at", "source": "Vehicle", "target": "GhostStation",
        "description": "", "cardinality": "", "inverse": ""}
    platform.ctx.save_versioned("ontology", document, version)
    # rejected by the platform's own publish gate, not by the kernel compiler
    with pytest.raises(ValidationError, match="ontology validation failed"):
        platform.ontologies.publish(wid, "admin")


# ----------------------------------------------------------------------
# 0.5 / 0.6 / 0.7 execution semantics
# ----------------------------------------------------------------------


def test_change_carries_seq_ts_execution_id(platform):
    wid = seed_workspace(platform)
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="change-1")
    gateway = platform.ctx.gateway_for(wid)
    entry = gateway.observe_changes(0)[-1]
    assert entry["source"] == "invoke"
    assert entry["seq"] > 0 and entry["ts"]
    assert entry["execution_id"] == result["execution_id"]
    changes = entry["changes"]
    assert changes and changes[0]["object"] == "Vehicle/V001"


def test_result_tristate(platform):
    wid = seed_workspace(platform)
    ok = platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1})
    assert ok["status"] == "succeeded"
    failed = platform.capabilities.invoke(wid, "admin", "Vehicle.nope")
    assert failed["status"] == "failed" and failed["ok"] is False
    capability = platform.capabilities.create(
        wid, "admin", key="boom", name="boom", target_type="Vehicle",
        implementation={"kind": "python", "source": '''
def execute(ctx):
    raise TimeoutError("executor timed out")
'''})
    platform.capabilities.publish(wid, capability["id"], "admin")
    unknown = platform.capabilities.invoke(
        wid, "admin", "Vehicle.boom", target="Vehicle/V001", args={})
    # unknown is a third state: neither success nor failure, and ok is false
    assert unknown["status"] == "unknown" and unknown["ok"] is False


def test_dead_loop_times_out(tmp_path):
    source = tmp_path / "spin.py"
    source.write_text(DEAD_LOOP_SOURCE, encoding="utf-8")
    gateway = KernelRuntimeGateway(store_kind="memory")
    gateway.publish_schema(SchemaPayload(
        schema=copy.deepcopy(SLOW_SCHEMA),
        bindings={"Vehicle.spin": {
            "executor": "python", "entrypoint": f"{source}:spin"}}))
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1", properties={"plate": "A"}),)))
    result = gateway.invoke("Vehicle.spin", target="Vehicle/V1", args={},
                            timeout=0.3)
    assert result.status == "unknown"
    assert result.ok is False
    assert "unknown" in result.error


def test_llm_timeout_is_unknown():
    from types import SimpleNamespace

    gateway = SimpleNamespace(
        describe=lambda: {"objects": [], "relations": [], "functions": []},
        describe_object=lambda name: {"name": name},
        find_objects=lambda t, f=None: [], related=lambda *a, **k: [],
        get_object=lambda ref: None,
        invoke=lambda *a, **k: SimpleNamespace(
            ok=True, status="succeeded", output={}, error=None, changes=()))
    with pytest.raises(TimeoutError):
        AgentRunner(gateway, FakeProvider(mode="timeout"), "", max_steps=3).run("go")


# ----------------------------------------------------------------------
# 0.8 actor / authorization
# ----------------------------------------------------------------------


def test_viewer_cannot_invoke_write(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "alice", "viewer", "admin")
    with pytest.raises(AccessDeniedError):
        platform.capabilities.invoke(
            wid, "alice", "Vehicle.recharge", target="Vehicle/V001",
            args={"amount": 1})


def test_agent_write_audited(platform):
    wid = seed_workspace(platform)
    script = [
        '{"think": "recharge", "action": {"tool": "invoke_capability", "args": '
        '{"function": "Vehicle.recharge", "target": "Vehicle/V002", '
        '"args": {"amount": 5}}}}',
        '{"think": "done", "answer": "ok"}',
    ]
    run = platform.intelligence.agent(
        wid, "admin", "recharge V002", provider=FakeProvider(script))
    assert run["ok"] is True
    executions = platform.ctx.repo.list("execution", wid)
    assert any(e.get("function") == "Vehicle.recharge"
               and e.get("actor") == "admin" for e in executions)
    audits = [a for a in platform.ctx.audit.list(wid)
              if a["action"] == "capability.invoke"]
    assert any(a["actor"] == "admin" for a in audits)


def test_provider_not_injectable(platform, http_server):
    from conftest import http_post

    base, wid = http_server
    status, body = http_post(
        base, f"/api/workspaces/{wid}/ontology/draft",
        {"text": "station", "llm": {"provider": "openai",
                                    "api_key": "sk-leak"}})
    assert status == 400
    assert "injection is disabled" in body["error"]


# ----------------------------------------------------------------------
# 1.2 / 1.3 atomic publish + events
# ----------------------------------------------------------------------


def _fresh_ontology_workspace(platform) -> str:
    wid = platform.workspaces.create("atomic", actor="admin")["id"]
    platform.ontologies.create(wid, "atomic", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True}])
    return wid


def test_publish_survives_crash_and_retries(platform):
    wid = _fresh_ontology_workspace(platform)
    original = platform.ontologies.publish._publisher.publish

    def crash(*args, **kwargs):
        raise ConnectionError("kernel connection dropped")

    platform.ontologies.publish._publisher.publish = crash
    try:
        with pytest.raises(ConnectionError):
            platform.ontologies.publish(wid, "admin")
    finally:
        platform.ontologies.publish._publisher.publish = original
    retried = platform.ontologies.publish(wid, "admin")
    assert retried["ontology"]["lifecycle"] == "published"
    assert len(platform.ctx.repo.list("release", wid)) == 1
    deployment = platform.ctx.repo.get("deployment", wid, "current")
    assert deployment and deployment["release_id"] == retried["release"]["id"]


def test_events_same_tx_as_state(platform):
    wid = _fresh_ontology_workspace(platform)
    published = platform.ontologies.publish(wid, "admin")
    assert published["ontology"]["lifecycle"] == "published"
    releases = len(platform.ctx.repo.list("release", wid))

    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "color", "type": "string"}])

    def explode(*args, **kwargs):
        raise RuntimeError("outbox write failed")

    original = EventLog.emit
    EventLog.emit = explode
    try:
        with pytest.raises(RuntimeError):
            platform.ontologies.publish(wid, "admin")
    finally:
        EventLog.emit = original
    assert len(platform.ctx.repo.list("release", wid)) == releases
    current = platform.ontologies.get(wid, "admin")
    assert current["lifecycle"] == "draft"


def test_failed_invocation_emits_event(platform):
    wid = seed_workspace(platform)
    platform.capabilities.invoke(wid, "admin", "Vehicle.nope")
    events = platform.events.list(wid, event_type="capability.invoked")
    assert events[-1]["payload"]["status"] == "failed"
    assert events[-1]["payload"]["ok"] is False


# ----------------------------------------------------------------------
# 1.4 contract verification
# ----------------------------------------------------------------------


def test_postcondition_violation_flagged(platform):
    wid = seed_workspace(platform)
    capability = platform.capabilities.create(
        wid, "admin", key="noop", name="noop", target_type="Vehicle",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": NOOP_SOURCE},
        contract=CONTRACT)
    platform.capabilities.publish(wid, capability["id"], "admin")
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.noop", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="noop-1")
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["verification"]["post"]["ok"] is False
    event = platform.events.list(wid, event_type="capability.verified")[-1]
    assert event["payload"]["ok"] is False


def test_recharge_balance_equals_old_plus_amount(platform):
    wid = seed_workspace(platform)
    old = _balance(platform, wid)
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 10})
    assert _balance(platform, wid) == old + 10
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 7})
    assert _balance(platform, wid) == old + 17


def test_projection_rejects_value_outside_enum(platform):
    wid = _constrained_workspace(platform)
    source = platform.data.connect_source(
        wid, "admin", "vehicles", "static",
        assets=[{"name": "vehicles", "config": {"rows": [
            {"plate": "A", "status": "flying"}]}}])
    projection = platform.data.create_projection(
        wid, "admin", "p", source["id"],
        object_mappings=[{
            "asset": "vehicles", "object_type": "Vehicle", "id_field": "plate",
            "fields": [{"source_field": "plate", "property": "plate"},
                       {"source_field": "status", "property": "status"}]}])
    with pytest.raises(ValidationError):
        platform.data.run_projection(wid, projection["id"], "admin")


# ----------------------------------------------------------------------
# 1.5 verifier / reconciler
# ----------------------------------------------------------------------


def test_verification_violation_observed(platform):
    wid = seed_workspace(platform)
    capability = platform.capabilities.create(
        wid, "admin", key="noop2", name="noop2", target_type="Vehicle",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": NOOP_SOURCE},
        contract=CONTRACT)
    platform.capabilities.publish(wid, capability["id"], "admin")
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.noop2", target="Vehicle/V001",
        args={"amount": 1}, idempotency_key="noop2-1")
    violated = result["verification"]["post"]["violated"]
    assert any(c["op"] == "eq_prev_plus" for c in violated)
    record = platform.ctx.repo.get("execution", wid, "key_noop2-1")
    assert record["verification"]["ok"] is False


def test_reconcile_reports_mismatch(platform):
    wid = seed_workspace(platform)
    assert platform.reconciler.reconcile(wid, "admin")["ok"] is True
    # un-owned extras are not this projection's drift
    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V999",
                     properties={"plate": "Z", "balance": 1}),)))
    assert platform.reconciler.reconcile(wid, "admin")["ok"] is True
    # deleting an object the projection owns is drift
    platform.ctx.gateway_for(wid).delete_objects(["Vehicle/V001"])
    report = platform.reconciler.reconcile(wid, "admin")
    assert report["ok"] is False
    assert "V001" in report["findings"][0]["missing_in_runtime"]
    assert "reconciliation.found_differences" not in {
        e["type"] for e in platform.events.list(wid)}
    platform.reconciler.reconcile(wid, "admin", emit=True)
    assert "reconciliation.found_differences" in {
        e["type"] for e in platform.events.list(wid)}


# ----------------------------------------------------------------------
# 1.6 projection v2
# ----------------------------------------------------------------------


def test_watermark_skips_unchanged(platform):
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    second = platform.data.run_projection(wid, projection["id"], "admin")
    assert second.get("skipped") is True and second["watermark"]
    forced = platform.data.run_projection(
        wid, projection["id"], "admin", force=True)
    assert not forced.get("skipped")


def test_projection_run_recorded(platform):
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    platform.data.run_projection(wid, projection["id"], "admin", force=True)
    runs = [r for r in platform.ctx.repo.list("projection_run", wid)
            if r["projection_id"] == projection["id"]]
    assert runs
    last = runs[-1]
    assert {"status", "watermark", "started_at", "finished_at", "summary"} <= set(last)
    assert last["status"] == "succeeded"


# ----------------------------------------------------------------------
# 1.7 / 1.8 saga + governance
# ----------------------------------------------------------------------


def test_step_k_failure_compensates_prior(platform):
    wid = seed_workspace(platform)
    assert _balance(platform, wid) == 100
    _publish_workflow(platform, wid, "saga", [
        {"kind": "invoke", "function": "Vehicle.recharge",
         "target": "{{target}}", "args": {"amount": 5}, "as": "debit",
         "compensate": {"kind": "invoke", "function": "Vehicle.recharge",
                        "target": "{{target}}", "args": {"amount": -5}}},
        {"kind": "invoke", "function": "Vehicle.nope", "target": "{{target}}"},
    ])
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.saga", target="Vehicle/V001",
        idempotency_key="saga-1")
    assert result["status"] == "failed"
    assert "compensated" in result["error"]
    assert _balance(platform, wid) == 100


def test_viewer_write_denied(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")
    with pytest.raises(AccessDeniedError):
        platform.ontologies.upsert_object(
            wid, "bob", "Road", [{"name": "name", "type": "string"}])


def test_last_owner_cannot_be_removed(platform):
    wid = platform.workspaces.create("owners", actor="admin")["id"]
    members = platform.workspaces.list_members(wid, "admin")
    owner = [m["user"] for m in members if m["role"] == "owner"]
    assert owner == ["admin"]
    with pytest.raises(ValidationError):
        platform.workspaces.remove_member(wid, "admin", "admin")


def test_illegal_transition_rejected():
    with pytest.raises(LifecycleError):
        transition("draft", "publish")
    with pytest.raises(LifecycleError):
        transition("published", "validate")
    with pytest.raises(LifecycleError):
        transition("deprecated", "publish")
    assert transition("validated", "revise") == "draft"


# ----------------------------------------------------------------------
# 1.10 error taxonomy + 0.5 structural object refs
# ----------------------------------------------------------------------


def test_error_codes_stable():
    from onto_platform.errors import (
        AccessDeniedError, ConcurrencyConflictError, ConnectorError,
        NotFoundError, classify,
    )

    cases = {
        ValidationError("bad"): ("validation", 400, False),
        AccessDeniedError("no"): ("access_denied", 403, False),
        NotFoundError("missing"): ("not_found", 404, False),
        ConcurrencyConflictError("stale"): ("conflict", 409, True),
        ConnectorError("down"): ("connector", 502, True),
    }
    for exc, (code, status, retryable) in cases.items():
        details = classify(exc)
        assert details["code"] == code
        assert details["http_status"] == status
        assert details["retryable"] is retryable
        assert details["type"] == type(exc).__name__
    internal = classify(RuntimeError("boom"))
    assert internal["code"] == "internal"
    assert internal["http_status"] == 500 and internal["retryable"] is False


def test_invalid_ref_rejected_at_construction():
    from onto_platform.domain.values import ObjectRef

    ref = ObjectRef.parse("Vehicle/V001")
    assert ref == ObjectRef("Vehicle", "V001")
    with pytest.raises(ValidationError):
        ObjectRef.parse("Vehicle")
    with pytest.raises(ValidationError):
        ObjectRef.parse("Vehicle/V001/extra")
    with pytest.raises(ValidationError):
        ObjectRef("", "V001")
    with pytest.raises(ValidationError):
        ObjectRef("Ve/hicle", "V001")
