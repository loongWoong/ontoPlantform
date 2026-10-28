"""Scheduler isolation and operator visibility (P1-11)."""

from __future__ import annotations

from conftest import make_platform, seed_workspace


def test_one_failing_task_does_not_stop_the_others():
    platform = make_platform()
    wid = seed_workspace(platform)
    scheduler = platform.start_scheduler(poll_seconds=60)
    try:
        scheduler.tasks.clear()
        calls = {"good": 0}

        def bad():
            raise RuntimeError("this task is broken")

        def good():
            calls["good"] += 1
            return "ok"

        scheduler.every("bad.task", 1, bad)
        scheduler.every("good.task", 1, good)
        results = scheduler.run_once()
        by_name = {row["task"]: row for row in results}
        assert by_name["bad.task"]["ok"] is False
        assert "broken" in by_name["bad.task"]["error"]
        assert by_name["good.task"]["ok"] is True
        assert calls["good"] == 1
        status = scheduler.status()
        assert status["running"] is True
        bad_row = [t for t in status["tasks"] if t["name"] == "bad.task"][0]
        assert bad_row["failures"] == 1 and bad_row["last_error"]
        assert bad_row["last_run_at"] and bad_row["next_run_at"]
        assert bad_row["next_run_at"] > bad_row["last_run_at"]
    finally:
        platform.stop_scheduler()


def test_run_once_named_task_ignores_its_interval():
    platform = make_platform()
    seed_workspace(platform)
    scheduler = platform.start_scheduler(poll_seconds=60)
    try:
        seen = []

        def action():
            seen.append(1)
            return len(seen)

        scheduler.every("manual.task", 3600, action)
        results = scheduler.run_once("manual.task")
        assert results[0]["ok"] is True
        assert seen == [1]
        assert scheduler.status()["tasks"][-1]["runs"] == 1
    finally:
        platform.stop_scheduler()
