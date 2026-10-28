"""Release ring isolation and restart truth (P0-5 / P1-5)."""

from __future__ import annotations

from conftest import make_platform, seed_workspace
from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta


def _platform():
    platform = make_platform()
    wid = seed_workspace(platform)
    return platform, wid


def test_canary_write_never_reaches_the_serving_runtime():
    platform, wid = _platform()
    release = platform.releases.list(wid, "admin")["releases"][-1]
    platform.releases.canary(wid, "admin", release["id"])
    canary = platform.ctx.gateway_for(wid, ring="canary")
    canary.apply_delta(SemanticDelta(upserts=[
        ObjectUpsert("TollStation", "STCANARY", {"name": "影子站",
                                                 "status": "free"})
    ]))
    assert canary.get_object("TollStation/STCANARY") is not None
    current = platform.ctx.gateway_for(wid)
    assert current.get_object("TollStation/STCANARY") is None


def test_stale_canary_check_blocks_promotion(monkeypatch):
    platform, wid = _platform()
    release = platform.releases.list(wid, "admin")["releases"][-1]
    platform.releases.canary(wid, "admin", release["id"])
    check = platform.releases.check_canary(wid, "admin")
    assert check["ok"] is True
    # make the check look old without waiting ten minutes
    check["checked_at"] = "2020-01-01T00:00:00+00:00"
    platform.ctx.save("canary_check", check)
    from onto_platform.errors import GateError

    try:
        platform.releases.promote_canary(wid, "admin", require_healthy=False)
    except GateError as exc:
        assert "stale" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("stale canary check must block promotion")


def test_failed_deploy_restores_previous_pointer_and_runtime():
    platform, wid = _platform()
    previous = platform.releases.list(wid, "admin")["deployment"]
    platform.ctx.repo.put("release", wid, "rel_broken", {
        "id": "rel_broken", "workspace_id": wid, "schema_version": 99,
        "schema": {"version": "0.2", "objects": "not-a-dict"},
        "bindings": {}, "workflows": {}, "capability_ids": [],
    })
    from onto_platform.errors import PlatformError

    try:
        platform.releases.deploy(wid, "admin", "rel_broken")
    except PlatformError:
        pass
    else:  # pragma: no cover
        raise AssertionError("broken release must fail")
    current = platform.releases.list(wid, "admin")["deployment"]
    assert current["release_id"] == previous["release_id"]
    assert current["status"] == "active"
    assert platform.events.list(wid, event_type="deployment.failed")
    # the serving runtime still has the previous schema, not the broken one
    assert platform.ctx.gateway_for(wid).describe()["functions"]


def test_sqlite_canary_check_works_while_audit_is_transactional(tmp_path):
    """A canary check writes state and audit in one sqlite transaction."""
    db = str(tmp_path / "platform.db")
    platform = build_platform(PlatformConfig(
        store="sqlite", db_path=db,
        runtime_data_dir=str(tmp_path / "runtime")))
    wid = seed_workspace(platform)
    release = platform.releases.list(wid, "admin")["releases"][-1]
    platform.releases.canary(wid, "admin", release["id"])
    check = platform.releases.check_canary(wid, "admin")
    assert check["ok"] is True, check
    assert platform.ctx.audit.list(wid)[0]["action"] == "canary.checked"
    platform.close()
