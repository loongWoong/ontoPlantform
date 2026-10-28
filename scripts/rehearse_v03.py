#!/usr/bin/env python3
"""Release and rollback rehearsal for v0.3 (WP5).

This is a destructive-free drill: it creates a throwaway SQLite platform in a
temporary directory, cuts and deploys two releases, rolls back, simulates a
process restart, then exercises the isolated canary gate.

It is not part of the unit test suite because it deliberately drives the real
SQLite repository and runtime files, which is slower than the in-memory tests.
Run it from the repository root:

    python scripts/rehearse_v03.py

Success prints ``REHEARSAL PASSED`` and exits 0.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import RECHARGE_SOURCE, seed_workspace  # noqa: E402
from onto_platform.bootstrap import PlatformConfig, build_platform  # noqa: E402
from onto_platform.errors import PlatformError  # noqa: E402
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def schema_for(platform, workspace_id: str) -> dict:
    return platform.ctx.gateway_for(workspace_id).describe()


def main() -> int:
    with tempfile.TemporaryDirectory(
            prefix="onto-rehearsal-", ignore_cleanup_errors=True) as tmp:
        db = str(Path(tmp) / "platform.db")
        config = PlatformConfig(store="sqlite", db_path=db,
                                runtime_data_dir=str(Path(tmp) / "runtime"))

        platform = build_platform(config)
        wid = seed_workspace(platform)
        print(f"workspace {wid}")

        baseline = platform.releases.list(wid, "admin")["deployment"]
        first = {"id": baseline["release_id"]}
        first_schema = schema_for(platform, wid)
        check(first_schema["functions"] != {}, "baseline release serves functions")

        platform.ontologies.revise(wid, "admin")
        platform.ontologies.upsert_object(
            wid, "admin", "Vehicle",
            properties=[{"name": "plate", "type": "string"},
                        {"name": "balance", "type": "number"},
                        {"name": "tier", "type": "string"}],
            description="车辆")
        published = platform.ontologies.publish(wid, "admin")
        second = {"id": published["release"]["id"]}
        active = platform.releases.list(wid, "admin")["deployment"]
        check(active["release_id"] == second["id"], "publish deploys a new release")
        check(first["id"] != second["id"],
              "publish creates a distinct immutable release")

        platform.releases.rollback(wid, "admin")
        rolled = platform.releases.list(wid, "admin")["deployment"]
        check(rolled["release_id"] == first["id"], "rollback restores first release")
        check(rolled["status"] == "active", "rollback leaves an active pointer")

        # a fresh platform on the same database is the process-restart path
        platform.close()
        restarted = build_platform(config)
        restarted_schema = schema_for(restarted, wid)
        restarted_active = restarted.releases.list(wid, "admin")["deployment"]
        check(restarted_active["release_id"] == first["id"],
              "restart reads the rolled-back pointer as truth")
        check(restarted_schema["functions"] == first_schema["functions"],
              "restart rebuilds the same runtime schema")
        mismatches = [e for e in restarted.events.list(
            wid, event_type="schema.fingerprint_mismatch")]
        check(mismatches == [], "restart emits no fingerprint mismatch")

        third = restarted.releases.cut(wid, "admin", note="canary candidate")
        restarted.releases.canary(wid, "admin", third["id"])
        current_before = restarted.releases.list(wid, "admin")["deployment"]
        check(current_before["release_id"] == first["id"],
              "canary does not move the serving pointer")
        check(restarted.releases.check_canary(wid, "admin")["ok"] is True,
              "canary health check passes")
        canary = restarted.ctx.gateway_for(wid, ring="canary")
        canary.apply_delta(SemanticDelta(upserts=[
            ObjectUpsert("Vehicle", "V-CANARY", {"plate": "鲁C00000",
                                                  "balance": 0})]))
        check(restarted.ctx.gateway_for(wid).get_object("Vehicle/V-CANARY")
              is None, "canary writes stay out of the serving runtime")

        restarted.releases.promote_canary(wid, "admin")
        promoted = restarted.releases.list(wid, "admin")["deployment"]
        check(promoted["release_id"] == third["id"],
              "promotion moves current to the checked canary")

        broken = restarted.releases.cut(wid, "admin", note="broken candidate")
        restarted.ctx.repo.put("release", wid, "rel_broken", {
            **broken, "id": "rel_broken", "schema_version": 99,
            "schema": {"version": "0.2", "objects": "not-a-dict"},
        })
        try:
            restarted.releases.deploy(wid, "admin", "rel_broken")
        except PlatformError:
            pass
        else:  # pragma: no cover
            raise AssertionError("broken release must fail to deploy")
        after_failure = restarted.releases.list(wid, "admin")["deployment"]
        check(after_failure["release_id"] == third["id"],
              "failed deploy restores the previous serving pointer")
        check(bool(restarted.events.list(wid, event_type="deployment.failed")),
              "failed deploy records deployment.failed")

        # capability source is part of the release contract; keep the import
        # visible so the drill stays close to the seeded product path
        check("def execute" in RECHARGE_SOURCE, "seeded capability source is present")
        restarted.close()
        print("REHEARSAL PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
