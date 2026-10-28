"""Phase 2.2: layered controller (propose -> arbitrate -> verify)."""

from __future__ import annotations

from conftest import seed_workspace


def test_controller_repairs_projection_drift(platform):
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    # simulate a row that vanished inside the runtime (drift)
    gateway.delete_objects(["TollStation/ST002"])

    proposal = platform.controller.propose(wid, "admin")
    assert proposal["ok"] is False
    assert [p["action"] for p in proposal["proposals"]] == ["run_projection"]

    result = platform.controller.act(wid, "admin")
    assert result["applied"], "an accepted proposal must be executed"
    assert result["verified"] is True
    assert gateway.get_object("TollStation/ST002") is not None
    # a healthy workspace proposes nothing and applies nothing
    quiet = platform.controller.act(wid, "admin")
    assert quiet["proposed"] == [] and quiet["applied"] == []


def test_controller_arbitration_rejects_unknown_actions(platform):
    wid = seed_workspace(platform)
    proposals = [
        {"action": "run_projection", "projection_id": "p1"},
        {"action": "run_projection", "projection_id": "p1"},  # duplicate
        {"action": "drop_database", "projection_id": "p1"},   # not allowed
    ]
    accepted = platform.controller.arbitrate(proposals)
    assert accepted == [{"action": "run_projection", "projection_id": "p1"}]
    assert platform.controller.arbitrate(proposals, allow={"drop_database"}) == [
        {"action": "drop_database", "projection_id": "p1"}]


def test_controller_act_writes_through_the_audited_use_case(platform):
    wid = seed_workspace(platform)
    platform.ctx.gateway_for(wid).delete_objects(["Vehicle/V001"])
    platform.controller.act(wid, "admin")
    runs = [r for r in platform.ctx.repo.list("projection_run", wid)]
    assert any(r.get("force") is True for r in runs)
    audits = [a["action"] for a in platform.ctx.audit.list(wid)]
    assert "data.run_projection" in audits
    assert "projection.run_completed" in {e["type"]
                                          for e in platform.events.list(wid)}
