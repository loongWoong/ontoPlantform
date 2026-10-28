"""v0.2 -> v0.3 migration is dry-run first and idempotent (WP5)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from onto_platform.bootstrap import PlatformConfig, build_platform

ROOT = Path(__file__).resolve().parents[1]


def test_migration_dry_run_then_apply(tmp_path):
    db = tmp_path / "platform.db"
    platform = build_platform(PlatformConfig(store="sqlite", db_path=str(db)))
    wid = platform.workspaces.create("迁移", actor="admin")["id"]
    platform.ctx.repo.put("execution", wid, "key_crashed", {
        "id": "key_crashed", "workspace_id": wid, "status": "running",
        "in_flight": True, "owner_instance": "inst_dead",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    script = ROOT / "scripts" / "migrate_v03.py"
    dry = subprocess.run(
        [sys.executable, str(script), "--db", str(db)],
        capture_output=True, text=True, cwd=ROOT)
    assert dry.returncode == 0, dry.stderr
    assert "would change" in dry.stdout
    assert "running->unknown" in dry.stdout
    # dry run did not touch the record
    record = platform.ctx.repo.get("execution", wid, "key_crashed")
    assert record["status"] == "running"

    applied = subprocess.run(
        [sys.executable, str(script), "--db", str(db), "--apply"],
        capture_output=True, text=True, cwd=ROOT)
    assert applied.returncode == 0, applied.stderr
    record = platform.ctx.repo.get("execution", wid, "key_crashed")
    assert record["status"] == "unknown"
    assert record["in_flight"] is False

    again = subprocess.run(
        [sys.executable, str(script), "--db", str(db), "--apply"],
        capture_output=True, text=True, cwd=ROOT)
    assert again.returncode == 0
    assert "nothing to migrate" in again.stdout
