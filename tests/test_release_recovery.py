"""Deployment ring and restart recovery tests (P0-4 / P0-5)."""

from __future__ import annotations

from onto_platform.application.recovery import recover_deployment
from conftest import make_platform, seed_workspace


def _good_platform():
    platform = make_platform()
    wid = seed_workspace(platform)
    return platform, wid


def test_pending_deployment_is_completed_on_restart():
    platform, wid = _good_platform()
    current = platform.releases.list(wid, "admin")["deployment"]
    # simulate a crash after the pending write, before the active pointer
    pending = {**current, "status": "pending",
               "release_id": current["release_id"],
               "base_release_id": current["release_id"]}
    platform.ctx.repo.put("deployment", wid, "current", pending)
    result = recover_deployment(platform.ctx, wid)
    assert result["recovered"] is True
    assert result["mode"] == "completed"
    deployment = platform.releases.list(wid, "admin")["deployment"]
    assert deployment["status"] == "active"
    assert platform.events.list(wid, event_type="schema.recovered")


def test_missing_intended_release_restores_base_and_never_serves_it():
    platform, wid = _good_platform()
    base = platform.releases.list(wid, "admin")["deployment"]
    platform.ctx.repo.put("deployment", wid, "current", {
        **base, "status": "pending", "release_id": "rel_missing",
        "base_release_id": base["release_id"],
    })
    result = recover_deployment(platform.ctx, wid)
    assert result["recovered"] is True
    assert result["mode"] == "restored"
    deployment = platform.releases.list(wid, "admin")["deployment"]
    assert deployment["release_id"] == base["release_id"]
    assert deployment["status"] == "active"
    assert platform.events.list(wid, event_type="deployment.failed")


def test_canary_ring_has_its_own_runtime():
    platform, wid = _good_platform()
    release = platform.releases.list(wid, "admin")["releases"][-1]
    serving_before = platform.releases.list(wid, "admin")["deployment"]
    staged = platform.releases.canary(wid, "admin", release["id"])
    assert staged["isolated"] is True
    assert staged["state_mode"] == "schema_only"
    # staging must not replace the serving pointer or schema
    current = platform.releases.list(wid, "admin")["deployment"]
    assert current["release_id"] == serving_before["release_id"]
    canary = platform.releases.list(wid, "admin")["canary"]
    assert canary["release_id"] == release["id"]
    assert canary["base_release_id"] == current["release_id"]
