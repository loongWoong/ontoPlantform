"""Phase 1.4-1.6: capability contracts, independent verifier, reconciler,
projection v2 (watermark / run records / idempotent runs).

Plan-v2 acceptance:
    test_postcondition_verified
    test_postcondition_violation_reported
    test_precondition_refuses_execution
    test_verify_workspace_is_read_only
    test_reconciler_reports_drift
    test_projection_watermark_skips_unchanged
"""

from __future__ import annotations

import copy
import json

import pytest

from onto_platform.errors import ValidationError

from conftest import seed_workspace

GOOD_SOURCE = """
def execute(ctx):
    vehicle = ctx.object()
    balance = vehicle.get("balance")
    new = balance + ctx.args["amount"]
    ctx.set(vehicle.ref, "balance", new)
    return {"success": True, "balance": new}
"""

NOOP_SOURCE = """
def execute(ctx):
    return {"success": True}
"""

CONTRACT = {
    "postconditions": [
        {"property": "balance", "op": "eq_prev_plus", "value": {"args": "amount"}},
        {"property": "balance", "op": "gte", "value": 0},
    ],
    "effects": ["Vehicle.balance"],
    "idempotent": False,
}


def _publish_capability(platform, wid, key, source, contract=None):
    capability = platform.capabilities.create(
        wid, "admin", key=key, name=key, target_type="Vehicle",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": source}, contract=contract)
    platform.capabilities.publish(wid, capability["id"], "admin")
    return capability


def test_contract_is_validated_at_creation(platform):
    wid = seed_workspace(platform)
    with pytest.raises(ValidationError, match="op"):
        platform.capabilities.create(
            wid, "admin", key="bad", name="bad", target_type="Vehicle",
            implementation={"kind": "python", "source": NOOP_SOURCE},
            contract={"postconditions": [{"property": "balance",
                                          "op": "approximately"}]})


def test_postcondition_verified(platform):
    wid = seed_workspace(platform)
    _publish_capability(platform, wid, "topup", GOOD_SOURCE, CONTRACT)
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.topup", target="Vehicle/V001",
        args={"amount": 10}, idempotency_key="topup-1")
    assert result["status"] == "succeeded"
    verification = result["verification"]
    assert verification["post"]["ok"] is True
    assert {c["property"] for c in verification["post"]["checks"]} == {"balance"}
    # the verifier's judgement is recorded on the execution and as an event
    record = platform.ctx.repo.get("execution", wid, "key_topup-1")
    assert record["verification"]["ok"] is True
    types = {e["type"] for e in platform.events.list(wid)}
    assert "capability.verified" in types


def test_postcondition_violation_reported(platform):
    wid = seed_workspace(platform)
    _publish_capability(platform, wid, "noop", NOOP_SOURCE, CONTRACT)
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.noop", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="noop-1")
    # the contract was violated, so the outcome is a failure, not a silent
    # success: ok is true only for status == succeeded (P0-1)
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["verification"]["post"]["ok"] is False
    violated = result["verification"]["post"]["violated"]
    assert any(c["op"] == "eq_prev_plus" for c in violated)
    event = [e for e in platform.events.list(wid, event_type="capability.verified")][-1]
    assert event["payload"]["ok"] is False


def test_precondition_refuses_execution(platform):
    wid = seed_workspace(platform)
    _publish_capability(platform, wid, "vip", GOOD_SOURCE, {
        "preconditions": [
            {"property": "balance", "op": "gte", "value": 1000},
        ],
        "postconditions": [],
    })
    result = platform.capabilities.invoke(
        wid, "admin", "Vehicle.vip", target="Vehicle/V001",
        args={"amount": 10}, idempotency_key="vip-1")
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert "precondition" in result["error"]
    # the world was not touched
    gateway = platform.ctx.gateway_for(wid)
    assert gateway.get_object("Vehicle/V001")["properties"]["balance"] == 100
    record = platform.ctx.repo.get("execution", wid, "key_vip-1")
    assert record["status"] == "failed"


def test_verify_workspace_is_read_only(platform):
    wid = seed_workspace(platform)
    before = platform.ctx.gateway_for(wid).observe_changes(0)
    result = platform.verifier.verify_workspace(wid, "admin")
    assert result["ok"] is True
    assert result["checked"] >= 4  # 2 vehicles + 2 stations from the projection
    after = platform.ctx.gateway_for(wid).observe_changes(0)
    assert len(after) == len(before), "verification must not change the world"


def test_reconciler_reports_drift(platform):
    wid = seed_workspace(platform)
    clean = platform.reconciler.reconcile(wid, "admin")
    assert clean["ok"] is True

    # an un-owned extra object is not this projection's drift (P0-6)
    from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta

    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V999",
                              properties={"plate": "鲁Z", "balance": 1}),)))
    assert platform.reconciler.reconcile(wid, "admin")["ok"] is True

    # an object this projection owned and materialized disappears from the
    # runtime: that is real drift
    platform.ctx.gateway_for(wid).delete_objects(["Vehicle/V001"])
    drift = platform.reconciler.reconcile(wid, "admin")
    assert drift["ok"] is False
    finding = drift["findings"][0]
    assert "V001" in finding["missing_in_runtime"]
    # reads do not emit; an explicit reconcile cycle does
    assert "reconciliation.found_differences" not in {
        e["type"] for e in platform.events.list(wid)}
    platform.reconciler.reconcile(wid, "admin", emit=True)
    assert "reconciliation.found_differences" in {
        e["type"] for e in platform.events.list(wid)}


def test_projection_watermark_skips_unchanged(platform):
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    second = platform.data.run_projection(wid, projection["id"], "admin")
    assert second.get("skipped") is True
    assert second["watermark"]

    forced = platform.data.run_projection(
        wid, projection["id"], "admin", force=True)
    assert not forced.get("skipped")
    runs = [r for r in platform.ctx.repo.list("projection_run", wid)
            if r["projection_id"] == projection["id"]]
    # seed run + skipped run + forced run: a skip is still an audit record
    assert len(runs) == 3
    assert runs[-2]["status"] == "skipped"
    assert runs[-1]["status"] == "succeeded"
    assert runs[-1]["watermark"] == forced["watermark"]


def test_source_deletion_propagates(platform):
    """plan-v2 1.6 / K5: a row that disappears upstream deletes the object."""
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    source = copy.deepcopy(platform.ctx.repo.list("datasource", wid)[0])
    source["assets"]["stations"]["config"]["rows"] = [
        r for r in source["assets"]["stations"]["config"]["rows"]
        if r["sid"] != "ST002"]
    source["assets"]["transactions"]["config"]["rows"] = [
        r for r in source["assets"]["transactions"]["config"]["rows"]
        if r["sid"] != "ST002"]
    platform.ctx.repo.put("datasource", wid, source["id"], source)

    result = platform.data.run_projection(wid, projection["id"], "admin")
    assert not result.get("skipped")
    assert result["objects_deleted"] == 1
    gateway = platform.ctx.gateway_for(wid)
    assert gateway.get_object("TollStation/ST002") is None
    # the relation to the removed station went with it
    assert gateway.related("Vehicle/V001", "passes") == ["TollStation/ST001"]
    changes = gateway.observe_changes()
    assert any(c["kind"] == "delete_object"
               and "TollStation/ST002" in json.dumps(c, ensure_ascii=False)
               for entry in changes for c in entry["changes"])

    # deletion is permanent: an unchanged source is skipped, no resurrection
    again = platform.data.run_projection(wid, projection["id"], "admin")
    assert again.get("skipped") is True
    assert gateway.get_object("TollStation/ST002") is None
    assert gateway.count_objects() == 3  # V001, V002, ST001


def test_projection_reruns_after_source_change(platform):
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    source = copy.deepcopy(platform.ctx.repo.list("datasource", wid)[0])
    source["assets"]["vehicles"]["config"]["rows"].append(
        {"vid": "V003", "plate": "鲁C", "balance": 5})
    platform.ctx.repo.put("datasource", wid, source["id"], source)

    result = platform.data.run_projection(wid, projection["id"], "admin")
    assert not result.get("skipped")
    runtime = platform.ctx.gateway_for(wid).find_objects("Vehicle", report=True)["objects"]
    assert "V003" in {o["id"] for o in runtime}
