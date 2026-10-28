"""Outbox delivery truth (P1-7 / P0-6)."""

from __future__ import annotations

from onto_platform.errors import ValidationError
from conftest import make_platform, seed_workspace


def _platform():
    platform = make_platform()
    wid = seed_workspace(platform)
    return platform, wid


def test_no_subscriber_is_skipped_not_falsely_delivered():
    platform, wid = _platform()
    event = platform.events.emit("release.cut", wid, "admin", "release",
                                 "rel_demo", payload={})
    result = platform.dispatcher.dispatch(wid)
    assert result["dispatched"] == 0
    assert result["skipped"] >= 1
    stored = platform.ctx.repo.get("event", wid, event["id"])
    assert stored["delivery"] == "no_subscribers"
    assert stored["dispatched_at"] is not None
    # not a dead letter: nothing failed, there was simply no consumer
    assert stored.get("dead_lettered_at") is None


def test_failing_subscriber_retries_then_dead_letters():
    platform, wid = _platform()
    calls = {"n": 0, "event_id": None}

    def failing(event):
        if event.get("id") != calls["event_id"]:
            raise RuntimeError("downstream down")
        calls["n"] += 1
        raise RuntimeError("downstream down")

    platform.dispatcher.subscribe("release.cut", failing)
    event = platform.events.emit("release.cut", wid, "admin", "release",
                                 "rel_demo", payload={})
    calls["event_id"] = event["id"]
    from onto_platform.application.events import MAX_DELIVERY_ATTEMPTS

    for _ in range(MAX_DELIVERY_ATTEMPTS):
        # force the backoff to be due so the test does not sleep
        stored = platform.ctx.repo.get("event", wid, event["id"])
        stored["next_attempt_at"] = None
        platform.ctx.save("event", stored)
        platform.dispatcher.dispatch(wid)
    stored = platform.ctx.repo.get("event", wid, event["id"])
    assert stored["attempts"] == MAX_DELIVERY_ATTEMPTS
    assert stored["delivery"] == "dead_letter"
    assert stored["dead_lettered_at"] is not None
    assert calls["n"] == MAX_DELIVERY_ATTEMPTS
    assert platform.events.dead_letters(wid)


def test_internal_consumer_materializes_a_notification():
    platform, wid = _platform()
    event = platform.events.emit("todo.opened", wid, "system", "todo",
                                 "todo_demo", payload={"title": "收敛失败"})
    result = platform.dispatcher.dispatch(wid)
    assert result["dispatched"] >= 1
    stored = platform.ctx.repo.get("event", wid, event["id"])
    assert stored["delivery"] == "delivered"
    assert "notification" in stored["consumer"]
    notification = platform.ctx.repo.get(
        "notification", wid, f"notif_{event['id']}")
    assert notification is not None
    assert notification["payload"]["title"] == "收敛失败"


def test_unknown_event_type_is_rejected():
    platform, wid = _platform()
    try:
        platform.events.emit("nope.happened", wid, "admin", "x", "1")
    except ValidationError:
        return
    raise AssertionError("unknown event types must not enter the outbox")
