"""P2-B: lease-based event claim for multi-worker outboxes.

The in-process ``handler_key`` registry is still process-local.  What changes
here is *ownership*: before a worker dispatches an event it claims it with an
optimistic compare-and-set and a lease, so two workers sharing the same
database do not both run the same handler.  A crashed worker's lease expires
and the event is retried (at-least-once, handlers stay idempotent by id).
"""

from __future__ import annotations

import pytest

from onto_platform.application.events import EventLog
from onto_platform.errors import ValidationError
from tests.conftest import seed_workspace


def _event(platform, wid, event_type="workspace.created"):
    return platform.events.emit(event_type, wid, "admin", "workspace", wid, {})


def test_claim_is_exclusive_while_the_lease_is_live(platform, empty_workspace):
    event = _event(platform, empty_workspace)
    log = EventLog(platform.ctx)

    claimed = log.claim(event, "worker-a", lease_seconds=60)

    assert claimed is not None
    assert claimed["claimed_by"] == "worker-a"
    assert claimed["claimed_at"]
    assert claimed["lease_expires_at"]
    assert log.claim(event, "worker-b", lease_seconds=60) is None
    assert all(row["id"] != event["id"]
               for row in log.pending(empty_workspace))


def test_expired_lease_can_be_reclaimed(platform, empty_workspace):
    event = _event(platform, empty_workspace)
    log = EventLog(platform.ctx)
    claimed = log.claim(event, "worker-a", lease_seconds=60)
    assert claimed is not None

    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    stored["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    platform.ctx.repo.put("event", empty_workspace, event["id"], stored)

    assert any(row["id"] == event["id"]
               for row in log.pending(empty_workspace))
    reclaimed = log.claim(event, "worker-b", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed["claimed_by"] == "worker-b"


def test_dispatcher_delivers_an_event_only_once_across_workers(
        platform, empty_workspace):
    calls: list[str] = []
    platform.dispatcher.subscribe(
        "workspace.created", lambda event: calls.append(event["event_id"]))
    event = _event(platform, empty_workspace)

    first = platform.dispatcher.dispatch(empty_workspace, worker_id="worker-a")
    second = platform.dispatcher.dispatch(empty_workspace, worker_id="worker-b")

    assert calls == [event["event_id"]]
    assert first["dispatched"] >= 1
    assert second["dispatched"] == 0
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"]
    assert stored["claimed_by"] is None


def test_failed_delivery_releases_the_claim_and_retries(
        platform, empty_workspace):
    attempts: list[int] = []

    def handler(event):
        attempts.append(1)
        raise RuntimeError("handler boom")

    platform.dispatcher.subscribe("workspace.created", handler)
    event = _event(platform, empty_workspace)

    first = platform.dispatcher.dispatch(empty_workspace, worker_id="worker-a")

    assert first["failed"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["claimed_by"] is None
    assert stored["attempts"] == 1
    assert "handler boom" in stored["last_error"]
    # backoff keeps it out of pending until the retry is due
    assert all(row["id"] != event["id"]
               for row in platform.events.pending(empty_workspace))

    stored["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
    platform.ctx.repo.put("event", empty_workspace, event["id"], stored)
    platform.dispatcher._subscribers["workspace.created"] = [
        lambda event: attempts.append(1)]
    second = platform.dispatcher.dispatch(empty_workspace, worker_id="worker-b")

    assert second["failed"] == 0
    assert second["dispatched"] >= 1
    assert len(attempts) == 2
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"]


def test_claim_validates_worker_and_lease(platform, empty_workspace):
    event = _event(platform, empty_workspace)
    log = EventLog(platform.ctx)

    with pytest.raises(ValidationError, match="worker_id"):
        log.claim(event, "")
    for lease in (0, -1, 3601, "30", True):
        with pytest.raises(ValidationError, match="lease_seconds"):
            log.claim(event, "worker", lease_seconds=lease)


def test_claim_uses_versioned_storage_on_sqlite(sqlite_platform):
    wid = seed_workspace(sqlite_platform)
    event = sqlite_platform.events.emit(
        "workspace.created", wid, "admin", "workspace", wid, {})
    log = EventLog(sqlite_platform.ctx)

    claimed = log.claim(event, "worker-a", lease_seconds=30)

    assert claimed is not None
    assert log.claim(event, "worker-b", lease_seconds=30) is None
