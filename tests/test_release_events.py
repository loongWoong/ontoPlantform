"""Phase 1.1-1.3: Release/Deployment, UnitOfWork, domain events/outbox.

Plan-v2 acceptance:
    test_failed_publish_leaves_no_release
    test_rollback_points_to_previous_release
    test_event_committed_with_state
    test_publish_survives_crash_and_retries   (simulated: handler fails once)
"""

from __future__ import annotations

import pytest

from onto_platform.errors import PlatformError, ValidationError

from conftest import make_platform, seed_workspace


def test_publish_cuts_release_and_deploys(platform):
    wid = seed_workspace(platform)
    listing = platform.releases.list(wid, "admin")
    assert listing["releases"], "publishing must cut at least one release"
    deployment = listing["deployment"]
    assert deployment["release_id"] == listing["releases"][-1]["id"]
    assert deployment["schema_version"] == 1

    # every publish is a new immutable artifact pointing at its predecessor
    releases = listing["releases"]
    assert len(releases) >= 2  # ontology.publish + capability.publish
    assert releases[-1]["previous_release_id"] == releases[-2]["id"]


def test_failed_publish_leaves_no_release(platform):
    wid = platform.workspaces.create("无本体", actor="admin")["id"]
    with pytest.raises(PlatformError):
        platform.releases.cut(wid, "admin")
    assert platform.ctx.repo.list("release", wid) == []
    assert platform.ctx.repo.get("deployment", wid, "current") is None


def test_rollback_points_to_previous_release(platform):
    wid = seed_workspace(platform)
    before = platform.releases.list(wid, "admin")["deployment"]

    # a publish that changes the runtime schema: a new capability
    cap = platform.capabilities.create(
        wid, "admin", key="charge", name="车辆扣费", target_type="Vehicle",
        implementation={"kind": "python", "source": (
            "def execute(ctx):\n"
            "    return {'success': True}\n")})
    platform.capabilities.publish(wid, cap["id"], "admin")
    after = platform.releases.list(wid, "admin")["deployment"]
    assert after["release_id"] != before["release_id"]
    runtime = platform.ontologies.describe_runtime(wid, "admin")
    assert "Vehicle.charge" in {f["name"] for f in runtime["functions"]}

    rolled = platform.releases.rollback(wid, "admin")
    assert rolled["deployment"]["release_id"] == before["release_id"]
    runtime = platform.ontologies.describe_runtime(wid, "admin")
    assert "Vehicle.charge" not in {f["name"] for f in runtime["functions"]}


def test_deploy_failure_keeps_previous_pointer(platform):
    wid = seed_workspace(platform)
    current = platform.releases.list(wid, "admin")["deployment"]
    platform.ctx.repo.put("release", wid, "broken", {
        "id": "broken", "workspace_id": wid, "schema_version": 99,
        "schema": {"version": "0.2", "objects": "not-a-dict"},
        "bindings": {}, "workflows": {}, "capability_ids": [],
    })
    with pytest.raises(PlatformError):
        platform.releases.deploy(wid, "admin", "broken")
    pointer = platform.releases.list(wid, "admin")["deployment"]
    assert pointer["release_id"] == current["release_id"]


def test_event_committed_with_state_and_dispatched(platform):
    wid = seed_workspace(platform)
    delivered: list[dict] = []
    platform.dispatcher.subscribe("capability.invoked", delivered.append)

    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1})
    events = platform.events.list(wid)
    invoked = [e for e in events if e["type"] == "capability.invoked"]
    assert invoked, "a write execution must emit a domain event"
    event = invoked[-1]
    # envelope contract (C3)
    assert event["event_id"].startswith("evt_")
    assert event["event_version"] == 1
    assert event["ts"] and event["workspace_id"] == wid
    assert event["aggregate"]["kind"] == "capability"
    assert event["execution_id"]

    result = platform.dispatcher.dispatch(wid)
    assert result["dispatched"] >= 1 and result["failed"] == 0
    assert delivered and delivered[-1]["event_id"] == event["event_id"]
    assert not [e for e in platform.events.list(wid)
                if not e["dispatched_at"]]
    again = platform.dispatcher.dispatch(wid)
    assert again["dispatched"] == 0


def test_failed_subscriber_is_retried(platform):
    wid = seed_workspace(platform)
    calls: list[str] = []

    def flaky(event: dict) -> None:
        calls.append(event["event_id"])
        if len(calls) == 1:
            raise RuntimeError("downstream unavailable")

    platform.dispatcher.subscribe("capability.invoked", flaky)
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 1})

    first = platform.dispatcher.dispatch(wid)
    assert first["failed"] >= 1
    pending = [e for e in platform.events.list(wid) if not e["dispatched_at"]]
    assert pending and pending[0]["attempts"] == 1
    assert pending[0]["last_error"]

    second = platform.dispatcher.dispatch(wid)
    # backoff: an event that just failed is not due yet
    assert second["dispatched"] == 0 and second["failed"] == 0
    assert len(calls) == 1
    pending = [e for e in platform.events.list(wid) if not e["dispatched_at"]]
    assert pending and pending[0]["attempts"] == 1
    assert pending[0]["next_attempt_at"]

    # once the backoff deadline passes the outbox retries it (at-least-once)
    for event in pending:
        event["next_attempt_at"] = None
        platform.ctx.repo.put("event", wid, event["id"], event)
    third = platform.dispatcher.dispatch(wid)
    assert third["dispatched"] >= 1 and third["failed"] == 0
    assert len(calls) == 2
    assert not [e for e in platform.events.list(wid) if not e["dispatched_at"]]


def test_transaction_rolls_back_all_writes(tmp_path):
    platform = make_platform()
    wid = platform.workspaces.create("事务", actor="admin")["id"]
    with pytest.raises(RuntimeError):
        with platform.ctx.transaction():
            platform.ctx.save("probe", {"id": "p1", "workspace_id": wid, "x": 1})
            raise RuntimeError("boom")
    assert platform.ctx.repo.get("probe", wid, "p1") is None


def test_sqlite_transaction_rolls_back(tmp_path):
    from onto_platform.bootstrap import PlatformConfig, build_platform

    platform = build_platform(PlatformConfig(
        store="sqlite", db_path=str(tmp_path / "p.db")))
    wid = platform.workspaces.create("事务", actor="admin")["id"]
    with pytest.raises(RuntimeError):
        with platform.ctx.transaction():
            platform.ctx.save("probe", {"id": "p1", "workspace_id": wid, "x": 1})
            raise RuntimeError("boom")
    assert platform.ctx.repo.get("probe", wid, "p1") is None


def test_unknown_event_type_is_rejected(platform):
    wid = platform.workspaces.create("事件", actor="admin")["id"]
    with pytest.raises(ValidationError):
        platform.events.emit("nope.happened", wid, "admin", "x", "1")


def test_canary_deploy_does_not_move_the_serving_pointer_then_promote(platform):
    wid = seed_workspace(platform)
    listed = platform.releases.list(wid, "admin")
    release = listed["releases"][-1]
    current_before = listed["deployment"]

    staged = platform.releases.canary(wid, "admin", release["id"])
    assert staged["deployment"]["id"] == "canary"
    assert staged["isolated"] is True
    assert staged["current"]["release_id"] == current_before["release_id"]
    check = platform.releases.check_canary(wid, "admin")
    assert check["ok"] is True

    promoted = platform.releases.promote_canary(wid, "admin")
    assert promoted["deployment"]["release_id"] == release["id"]
    assert promoted["canary"]["promoted"] is True
    types = {e["type"] for e in platform.events.list(wid)}
    assert {"release.canary_deployed", "release.canary_promoted"} <= types
    assert platform.releases.list(wid, "admin")["canary"]["promoted"] is True


def test_canary_promotion_can_require_a_healthy_slo(platform):
    wid = seed_workspace(platform)
    release = platform.releases.list(wid, "admin")["releases"][-1]
    platform.releases.canary(wid, "admin", release["id"])
    platform.capabilities.invoke(wid, "admin", "Vehicle.nope")
    assert platform.releases.check_canary(wid, "admin")["ok"] is True
    with pytest.raises(ValidationError) as excinfo:
        platform.releases.promote_canary(wid, "admin", require_healthy=True)
    assert "SLO" in str(excinfo.value)
    # without the SLO gate the promotion is still possible (operator decision)
    assert platform.releases.promote_canary(
        wid, "admin", require_healthy=False)["deployment"]
