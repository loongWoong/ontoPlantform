"""P2.2-C: durable handler registry + per-(event, handler) delivery ledger.

P2-B let workers *claim an event*; the handler registry was still a process
dictionary, so a key registered in worker B was unknown to worker A and a
pending event had no per-consumer state.  These tests pin the durable
contract:

* ``event_handler`` rows are workspace-scoped, keyed by ``handler_key`` and
  updated (never duplicated) on re-registration;
* every ``(event, handler_key)`` pair gets one ``event_delivery`` row whose
  claim is a CAS + lease, so two workers racing execute a handler only once;
* an event is only stamped ``dispatched_at`` when all its delivery rows are
  terminal; a key without a local callback leaves the event pending (nothing
  is dropped) and a later registration can still deliver it.

The legacy in-process path (``subscribe`` / ``subscribe_key`` /
``no_subscribers``) is covered by ``tests/test_event_subscriptions.py`` and
``tests/test_p2_event_claim.py`` and must keep working unchanged.
"""

from __future__ import annotations

import hashlib

import pytest

from onto_platform.application.event_handlers import (
    DELIVERY_MAX_ATTEMPTS,
    DeliveryTracker,
    HandlerRegistry,
    delivery_id,
)
from onto_platform.application.events import Dispatcher
from onto_platform.errors import NotFoundError, ValidationError

EVENT_TYPE = "workspace.created"


def _event(platform, wid, event_type: str = EVENT_TYPE) -> dict:
    return platform.events.emit(
        event_type, wid, "admin", "workspace", wid, {})


def _registry(platform) -> HandlerRegistry:
    return HandlerRegistry(platform.ctx)


def _dispatcher(platform, **kwargs) -> Dispatcher:
    return Dispatcher(platform.ctx, **kwargs)


def _delivery(platform, wid, event_id: str, key: str) -> dict:
    return DeliveryTracker(platform.ctx).get_or_create(wid, event_id, key)


def _force_due(platform, kind: str, wid: str, doc_id: str) -> None:
    """Rewrite the retry clock so a backed-off row is claimable again."""
    row = platform.ctx.repo.get(kind, wid, doc_id)
    assert row is not None
    row["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
    if "lease_expires_at" in row:
        row["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    platform.ctx.repo.put(kind, wid, doc_id, row)


# ----------------------------------------------------------------------
# registry
# ----------------------------------------------------------------------


def test_registry_register_is_persistent_and_upserts_by_key(
        platform, empty_workspace):
    reg = _registry(platform)
    row = reg.register(empty_workspace, "billing_handler", [EVENT_TYPE],
                       actor="admin")

    assert row["id"].startswith("evh_")
    assert row["handler_key"] == "billing_handler"
    assert row["kind"] == "in_process"
    assert row["active"] is True
    assert row["version"] == 1
    assert row["last_heartbeat_at"]
    assert platform.ctx.repo.get(
        "event_handler", empty_workspace, row["id"])["handler_key"] == (
            "billing_handler")

    updated = reg.register(empty_workspace, "billing_handler",
                           ["ontology.published"], actor="admin",
                           lease_seconds=10)
    assert updated["id"] == row["id"], "same key updates, never duplicates"
    assert updated["version"] == 2
    assert updated["event_types"] == ["ontology.published"]
    assert updated["lease_seconds"] == 10
    assert len(reg.list(empty_workspace)) == 1

    heartbeated = reg.heartbeat(empty_workspace, "billing_handler",
                                worker_id="worker-a")
    assert heartbeated["version"] == 3
    assert heartbeated["last_worker"] == "worker-a"

    paused = reg.pause(empty_workspace, "billing_handler")
    assert paused["active"] is False
    event = _event(platform, empty_workspace)
    assert reg.resolve(event) == []
    assert reg.activate(empty_workspace, "billing_handler")["active"] is True


def test_registry_is_workspace_scoped(platform, empty_workspace):
    other = platform.workspaces.create("另一个空间", actor="admin")["id"]
    reg = _registry(platform)
    mine = reg.register(empty_workspace, "shared_key", [EVENT_TYPE])
    theirs = reg.register(other, "shared_key", [EVENT_TYPE])

    assert mine["id"] != theirs["id"]
    assert reg.list(other) == [theirs]
    assert reg.list(empty_workspace) == [mine]
    assert [row["id"] for row in reg.resolve(
        _event(platform, other))] == [theirs["id"]]


def test_registry_validates_inputs_and_unknown_workspace(platform):
    reg = _registry(platform)
    wid = platform.workspaces.create("校验空间", actor="admin")["id"]

    with pytest.raises(ValidationError, match="handler_key"):
        reg.register(wid, "", [EVENT_TYPE])
    with pytest.raises(ValidationError, match="handler_key"):
        reg.register(wid, "bad key", [EVENT_TYPE])
    with pytest.raises(ValidationError, match="event_types"):
        reg.register(wid, "k", [])
    with pytest.raises(ValidationError, match="invalid event name"):
        reg.register(wid, "k", ["Not.An.Event"])
    with pytest.raises(ValidationError, match="kind"):
        reg.register(wid, "k", [EVENT_TYPE], kind="queue")
    with pytest.raises(ValidationError, match="endpoint"):
        reg.register(wid, "k", [EVENT_TYPE], kind="http")
    with pytest.raises(ValidationError, match="secret"):
        reg.register(wid, "k", [EVENT_TYPE], kind="http",
                     endpoint="http://example.invalid/hook")
    with pytest.raises(ValidationError, match="lease_seconds"):
        reg.register(wid, "k", [EVENT_TYPE], lease_seconds=0)
    with pytest.raises(NotFoundError, match="workspace not found"):
        reg.register("no-such-workspace", "k", [EVENT_TYPE])
    with pytest.raises(NotFoundError):
        reg.heartbeat(wid, "missing_key")
    with pytest.raises(NotFoundError):
        reg.pause(wid, "missing_key")


def test_registry_resolve_only_returns_active_matching_handlers(
        platform, empty_workspace):
    reg = _registry(platform)
    reg.register(empty_workspace, "h1", [EVENT_TYPE])
    reg.register(empty_workspace, "h2", ["ontology.published"])

    event = _event(platform, empty_workspace)
    assert [row["handler_key"] for row in reg.resolve(event)] == ["h1"]

    reg.pause(empty_workspace, "h1")
    assert reg.resolve(event) == []


# ----------------------------------------------------------------------
# shared registry across workers
# ----------------------------------------------------------------------


def test_two_dispatchers_share_registry_and_only_the_owner_runs(
        platform, empty_workspace):
    _registry(platform).register(empty_workspace, "ops", [EVENT_TYPE])
    worker_a = _dispatcher(platform)
    worker_b = _dispatcher(platform)
    calls: list[str] = []
    worker_b.register_handler("ops", lambda event: calls.append(event["id"]),
                              workspace_id=empty_workspace)

    event = _event(platform, empty_workspace)

    first = worker_a.dispatch(empty_workspace, worker_id="worker-a")
    assert first["dispatched"] == 0 and first["skipped"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is None
    assert stored["claimed_by"] is None, "a deferred event releases its claim"
    pending = _delivery(platform, empty_workspace, event["id"], "ops")
    assert pending["status"] == "pending"
    assert calls == []

    second = worker_b.dispatch(empty_workspace, worker_id="worker-b")
    assert second["dispatched"] >= 1
    assert calls == [event["id"]]
    delivered = platform.ctx.repo.get(
        "event_delivery", empty_workspace, delivery_id(event["id"], "ops"))
    assert delivered["status"] == "delivered"
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None
    assert stored["delivery"] == "delivered"

    worker_b.dispatch(empty_workspace, worker_id="worker-b")
    assert calls == [event["id"]], "a delivered event is not re-delivered"


def test_delivery_claim_is_exclusive_and_expired_lease_is_reclaimable(
        platform, empty_workspace):
    event = _event(platform, empty_workspace)
    delivery = _delivery(platform, empty_workspace, event["id"], "ops")
    tracker = DeliveryTracker(platform.ctx)

    claimed = tracker.claim(delivery, "worker-a", lease_seconds=60)
    assert claimed is not None and claimed["claimed_by"] == "worker-a"
    assert tracker.claim(delivery, "worker-b", lease_seconds=60) is None

    _force_due(platform, "event_delivery", empty_workspace, delivery["id"])
    taken_over = tracker.claim(delivery, "worker-b", lease_seconds=60)
    assert taken_over is not None and taken_over["claimed_by"] == "worker-b"


def test_worker_crash_lease_expiry_lets_another_worker_finish(
        platform, empty_workspace):
    _registry(platform).register(empty_workspace, "ops", [EVENT_TYPE])
    event = _event(platform, empty_workspace)
    # a worker claims the event, then crashes before doing any delivery
    platform.events.claim(event, "crashed-worker", lease_seconds=60)
    _force_due(platform, "event", empty_workspace, event["id"])

    survivor = _dispatcher(platform)
    calls: list[str] = []
    survivor.register_handler("ops", lambda item: calls.append(item["id"]),
                              workspace_id=empty_workspace)

    result = survivor.dispatch(empty_workspace, worker_id="worker-b")

    assert result["dispatched"] >= 1
    assert calls == [event["id"]]
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None


# ----------------------------------------------------------------------
# pending / terminal semantics
# ----------------------------------------------------------------------


def test_event_stays_pending_until_a_handler_is_registered(
        platform, empty_workspace):
    platform.ctx.repo.put("event_subscription", empty_workspace, "sub_ops", {
        "id": "sub_ops",
        "workspace_id": empty_workspace,
        "name": "ops",
        "event_type": EVENT_TYPE,
        "filter": {},
        "handler_key": "ops",
        "enabled": True,
        "created_at": platform.ctx.clock(),
    })
    dispatcher = _dispatcher(platform)
    calls: list[str] = []

    event = _event(platform, empty_workspace)
    first = dispatcher.dispatch(empty_workspace)
    assert first["skipped"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is None
    assert "ops" in (stored.get("last_error") or "")
    assert _delivery(
        platform, empty_workspace, event["id"], "ops")["status"] == "pending"

    dispatcher.subscribe_key("ops", lambda item: calls.append(item["id"]))
    second = dispatcher.dispatch(empty_workspace)
    assert second["dispatched"] >= 1
    assert calls == [event["id"]]
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None
    assert stored["attempts"] == 0, "a deferred event is not a failed attempt"


def test_event_dispatched_only_when_every_delivery_is_terminal(
        platform, empty_workspace):
    reg = _registry(platform)
    reg.register(empty_workspace, "h1", [EVENT_TYPE])
    reg.register(empty_workspace, "h2", [EVENT_TYPE])
    dispatcher = _dispatcher(platform)
    calls: list[str] = []
    dispatcher.register_handler("h1", lambda item: calls.append("h1"),
                                workspace_id=empty_workspace)

    event = _event(platform, empty_workspace)
    first = dispatcher.dispatch(empty_workspace, worker_id="worker-a")
    assert first["dispatched"] == 0 and first["skipped"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is None
    assert delivery_id(event["id"], "h1") and platform.ctx.repo.get(
        "event_delivery", empty_workspace,
        delivery_id(event["id"], "h1"))["status"] == "delivered"
    assert platform.ctx.repo.get(
        "event_delivery", empty_workspace,
        delivery_id(event["id"], "h2"))["status"] == "pending"

    dispatcher.register_handler("h2", lambda item: calls.append("h2"),
                                workspace_id=empty_workspace)
    second = dispatcher.dispatch(empty_workspace, worker_id="worker-b")
    assert second["dispatched"] >= 1
    assert calls == ["h1", "h2"], "the completed delivery does not run again"
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None


def test_failing_handler_keeps_event_undispatched_and_retries(
        platform, empty_workspace):
    _registry(platform).register(empty_workspace, "ops", [EVENT_TYPE])
    dispatcher = _dispatcher(platform)
    attempts: list[int] = []

    def broken(event: dict) -> None:
        attempts.append(1)
        raise RuntimeError("handler boom")

    dispatcher.register_handler("ops", broken, workspace_id=empty_workspace)
    event = _event(platform, empty_workspace)

    first = dispatcher.dispatch(empty_workspace, worker_id="worker-a")
    assert first["failed"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is None
    assert stored["attempts"] == 1
    assert "handler boom" in (stored.get("last_error") or "")
    delivery = platform.ctx.repo.get(
        "event_delivery", empty_workspace, delivery_id(event["id"], "ops"))
    assert delivery["status"] == "pending"
    assert delivery["attempts"] == 1

    dispatcher.register_handler("ops", lambda item: attempts.append(1),
                                workspace_id=empty_workspace)
    _force_due(platform, "event", empty_workspace, event["id"])
    _force_due(platform, "event_delivery", empty_workspace, delivery["id"])
    second = dispatcher.dispatch(empty_workspace, worker_id="worker-b")

    assert second["failed"] == 0 and second["dispatched"] >= 1
    assert len(attempts) == 2
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None


def test_delivery_dead_after_retry_budget(platform, empty_workspace):
    event = _event(platform, empty_workspace)
    delivery = _delivery(platform, empty_workspace, event["id"], "ops")
    tracker = DeliveryTracker(platform.ctx)
    for _ in range(DELIVERY_MAX_ATTEMPTS):
        claimed = tracker.claim(delivery, "worker-a", lease_seconds=60)
        assert claimed is not None
        tracker.mark_failed(claimed, "worker-a", "boom")
        _force_due(platform, "event_delivery", empty_workspace, delivery["id"])

    final = platform.ctx.repo.get(
        "event_delivery", empty_workspace, delivery["id"])
    assert final["status"] == "dead"
    assert final["attempts"] == DELIVERY_MAX_ATTEMPTS
    assert tracker.claim(final, "worker-b") is None, "dead rows never run again"
    assert tracker.for_event(empty_workspace, event["id"])[0]["status"] == "dead"


def test_delivery_id_is_deterministic_sha256(platform, empty_workspace):
    expected = hashlib.sha256(b"evt-1|ops").hexdigest()
    assert delivery_id("evt-1", "ops") == expected
    event = _event(platform, empty_workspace)
    first = _delivery(platform, empty_workspace, event["id"], "ops")
    second = _delivery(platform, empty_workspace, event["id"], "ops")
    assert first["id"] == second["id"] == delivery_id(event["id"], "ops")
    assert len(DeliveryTracker(platform.ctx).for_event(
        empty_workspace, event["id"])) == 1


# ----------------------------------------------------------------------
# http handler
# ----------------------------------------------------------------------


def test_http_handler_delivers_through_the_injected_transport(
        platform, empty_workspace):
    _registry(platform).register(
        empty_workspace, "webhook", [EVENT_TYPE], kind="http",
        endpoint="http://example.invalid/hook", secret="s3cret")
    requests: list[dict] = []
    dispatcher = _dispatcher(platform, transport=requests.append)
    event = _event(platform, empty_workspace)

    result = dispatcher.dispatch(empty_workspace, worker_id="worker-a")

    assert result["dispatched"] >= 1
    assert requests[0]["handler_key"] == "webhook"
    assert requests[0]["url"] == "http://example.invalid/hook"
    assert requests[0]["secret"] == "s3cret"
    assert requests[0]["event"]["id"] == event["id"]
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None


def test_http_handler_failure_retries(platform, empty_workspace):
    _registry(platform).register(
        empty_workspace, "webhook", [EVENT_TYPE], kind="http",
        endpoint="http://example.invalid/hook", secret="s3cret")

    def broken_transport(request: dict) -> None:
        raise RuntimeError("http 500")

    dispatcher = _dispatcher(platform, transport=broken_transport)
    event = _event(platform, empty_workspace)
    first = dispatcher.dispatch(empty_workspace, worker_id="worker-a")

    assert first["failed"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is None

    ok = _dispatcher(platform, transport=lambda request: None)
    _force_due(platform, "event", empty_workspace, event["id"])
    _force_due(platform, "event_delivery", empty_workspace,
               delivery_id(event["id"], "webhook"))
    second = ok.dispatch(empty_workspace, worker_id="worker-b")
    assert second["failed"] == 0 and second["dispatched"] >= 1
    stored = platform.ctx.repo.get("event", empty_workspace, event["id"])
    assert stored["dispatched_at"] is not None
