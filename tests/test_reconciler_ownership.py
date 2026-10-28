"""Projection ownership: only owned objects count as drift (P1-6)."""

from __future__ import annotations

from conftest import seed_workspace
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta


def test_unowned_extra_is_not_drift_but_owned_deletion_is(platform):
    wid = seed_workspace(platform)
    assert platform.reconciler.reconcile(wid, "admin")["ok"] is True
    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V999",
                              properties={"plate": "鲁Z", "balance": 1}),)))
    # a hand-created object is not owned by the projection
    assert platform.reconciler.reconcile(wid, "admin")["ok"] is True
    platform.ctx.gateway_for(wid).delete_objects(["Vehicle/V001"])
    drift = platform.reconciler.reconcile(wid, "admin")
    assert drift["ok"] is False
    assert "V001" in drift["findings"][0]["missing_in_runtime"]
    assert drift["findings"][0]["ownership"] == "additive"


def test_authoritative_projection_reports_unowned_orphans(platform):
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    projection["mode"] = "authoritative"
    platform.ctx.save("projection", projection)
    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Vehicle", id="V999",
                              properties={"plate": "鲁Z", "balance": 1}),)))
    drift = platform.reconciler.reconcile(wid, "admin")
    assert drift["ok"] is False
    assert "V999" in drift["findings"][0]["missing_in_source"]
    assert drift["findings"][0]["ownership"] == "authoritative"


def test_row_filter_is_applied_before_drift_comparison(platform):
    wid = seed_workspace(platform)
    projection = platform.data.list_projections(wid, "admin")[0]
    projection["object_mappings"][0]["filter"] = {"vid": "V001"}
    platform.ctx.save("projection", projection)
    platform.ctx.gateway_for(wid).delete_objects(["Vehicle/V002"])
    # V002 is excluded by the projection filter: its absence is expected
    assert platform.reconciler.reconcile(wid, "admin")["ok"] is True
