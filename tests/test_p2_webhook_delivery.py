"""P2.2-B: per-(event, webhook) delivery ledger, quota, retry and dead letters.

The ledger upgrades webhook delivery from aggregate counters on the
subscription row to one durable row per ``(event, webhook)`` pair.  These
tests use an injected transport (never a socket) and a controllable clock so
lease expiry, fixed-window quota and exponential backoff are deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from onto_platform.application.security.distribution import WebhookService
from onto_platform.application.security.webhook_delivery import (
    delivery_id_for,
    parse_ts,
)
from onto_platform.errors import AccessDeniedError, NotFoundError, ValidationError


class FakeClock:
    """Deterministic injectable clock (ISO strings, UTC)."""

    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> str:
        return self._now.isoformat(timespec="seconds")

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class RecordingTransport:
    """Injected transport: records calls and returns scripted outcomes."""

    def __init__(self, handler=None, default: int = 200) -> None:
        self.calls: list[dict] = []
        self.handler = handler
        self.default = default

    def __call__(self, url: str, body: bytes, headers: dict,
                 timeout: float) -> int:
        self.calls.append({"url": url, "body": body,
                           "headers": dict(headers)})
        if self.handler is None:
            return self.default
        return self.handler(url, body, headers, len(self.calls))


def _service(platform, transport, clock: FakeClock | None = None, **kwargs):
    service = WebhookService(platform.ctx, platform.security,
                             transport=transport, **kwargs)
    if clock is not None:
        service._clock = clock
        service.ledger._clock = clock
    return service


def _emit(platform, workspace_id: str, **payload):
    return platform.events.emit(
        "settings.changed", workspace_id, "admin", "settings",
        workspace_id, payload)


def _event_row(platform, workspace_id: str, event_id: str) -> dict:
    for row in platform.ctx.repo.list("event", workspace_id):
        if row.get("event_id") == event_id:
            return dict(row)
    raise AssertionError(f"event row not found: {event_id!r}")


def _delivery(service, workspace_id: str, delivery_id: str) -> dict:
    rows = service.list_deliveries(workspace_id)
    match = [row for row in rows if row.get("id") == delivery_id]
    assert match, f"delivery not found: {delivery_id!r}"
    return match[0]


# ----------------------------------------------------------------------
# retry / attempts
# ----------------------------------------------------------------------


def test_failed_delivery_retries_then_delivers_exactly_once(
        platform, empty_workspace):
    wid = empty_workspace
    statuses = [500, 200]
    transport = RecordingTransport(
        handler=lambda url, body, headers, n: statuses.pop(0))
    service = _service(platform, transport)
    hook = service.create_webhook(
        wid, "admin", "https://hooks.example.test/retry",
        ["settings.changed"], "s3cret")
    event = _emit(platform, wid, n=1)
    expected_id = delivery_id_for(event["event_id"], hook["id"])

    first = service.dispatch_webhooks()
    assert first["delivered"] == 0
    assert first["failed"] == 1
    assert "500" in first["errors"][0]

    row = _delivery(service, wid, expected_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert row["last_status_code"] == 500
    assert row["next_attempt_at"], "a retry must be scheduled"
    assert row["lease_expires_at"] is None, "the lease is released"
    stored_hook = platform.ctx.repo.get("webhook", wid, hook["id"])
    assert stored_hook["delivery_count"] == 0
    assert "500" in stored_hook["last_error"]
    assert not _event_row(platform, wid, event["event_id"]).get(
        "webhook_delivered_at")

    second = service.dispatch_webhooks()
    assert second["delivered"] == 1
    assert second["failed"] == 0
    assert len(transport.calls) == 2
    row = _delivery(service, wid, expected_id)
    assert row["status"] == "delivered"
    assert row["attempts"] == 2
    assert row["lease_expires_at"] is None
    assert _event_row(platform, wid, event["event_id"])["webhook_delivered_at"]
    stored_hook = platform.ctx.repo.get("webhook", wid, hook["id"])
    assert stored_hook["delivery_count"] == 1
    assert stored_hook["last_error"] is None

    # A repeated dispatch must not deliver the same pair twice.
    third = service.dispatch_webhooks()
    assert third["delivered"] == 0
    assert len(transport.calls) == 2
    assert _delivery(service, wid, expected_id)["attempts"] == 2


def test_exponential_backoff_blocks_retry_until_due(platform, empty_workspace):
    wid = empty_workspace
    clock = FakeClock()
    transport = RecordingTransport(default=500)
    service = _service(platform, transport, clock=clock)
    service.create_webhook(
        wid, "admin", "https://hooks.example.test/backoff",
        ["settings.changed"], "s3cret")
    _emit(platform, wid, n=1)

    assert service.dispatch_webhooks()["failed"] == 1
    # The first retry stays immediate for compatibility with the single-worker
    # dispatcher; the second failure starts the exponential wait.
    assert service.dispatch_webhooks()["failed"] == 1
    assert len(transport.calls) == 2
    row = service.list_deliveries(wid)[0]
    assert row["attempts"] == 2
    assert row["status"] == "failed"
    due = parse_ts(row["next_attempt_at"])
    assert due is not None and due > parse_ts(clock())

    blocked = service.dispatch_webhooks()
    assert blocked["failed"] == 0
    assert blocked["delivered"] == 0
    assert len(transport.calls) == 2, "backoff must not call the transport"

    clock.advance(11)  # attempts=2 -> 10s backoff
    after = service.dispatch_webhooks()
    assert after["failed"] == 1
    assert len(transport.calls) == 3
    assert service.list_deliveries(wid)[0]["attempts"] == 3


# ----------------------------------------------------------------------
# dead letters / event marker
# ----------------------------------------------------------------------


def test_max_attempts_dead_letters_and_event_marker_waits_for_all_hooks(
        platform, empty_workspace):
    wid = empty_workspace
    clock = FakeClock()

    def handler(url, body, headers, call_no):
        return 500 if url.endswith("/bad") else 200

    transport = RecordingTransport(handler=handler)
    service = _service(platform, transport, clock=clock)
    bad = service.create_webhook(
        wid, "admin", "https://hooks.example.test/bad",
        ["settings.changed"], "s3cret", max_attempts=3)
    service.create_webhook(
        wid, "admin", "https://hooks.example.test/good",
        ["settings.changed"], "s3cret")
    event = _emit(platform, wid, n=1)

    first = service.dispatch_webhooks()
    assert first["delivered"] == 1
    assert first["failed"] == 1
    assert not _event_row(platform, wid, event["event_id"]).get(
        "webhook_delivered_at"), "the failing hook is not terminal yet"

    assert service.dispatch_webhooks()["failed"] == 1
    row = service.list_deliveries(wid, webhook_id=bad["id"])[0]
    assert row["status"] == "failed" and row["attempts"] == 2
    assert not _event_row(platform, wid, event["event_id"]).get(
        "webhook_delivered_at")

    assert service.dispatch_webhooks()["failed"] == 0  # inside backoff window
    clock.advance(11)
    fourth = service.dispatch_webhooks()
    assert fourth["delivered"] == 0
    assert fourth["failed"] == 1
    assert fourth["dead_lettered"] == 1

    row = service.list_deliveries(wid, webhook_id=bad["id"])[0]
    assert row["status"] == "dead"
    assert row["attempts"] == 3
    assert row["next_attempt_at"] is None
    assert _event_row(platform, wid, event["event_id"])["webhook_delivered_at"]
    dead = service.dead_letters(wid)
    assert [item["id"] for item in dead] == [row["id"]]


# ----------------------------------------------------------------------
# replay
# ----------------------------------------------------------------------


def test_replay_dead_delivery_rearms_event(platform, empty_workspace):
    wid = empty_workspace
    clock = FakeClock()
    state = {"fail": True}

    def handler(url, body, headers, call_no):
        return 500 if state["fail"] else 200

    transport = RecordingTransport(handler=handler)
    service = _service(platform, transport, clock=clock)
    hook = service.create_webhook(
        wid, "admin", "https://hooks.example.test/replay",
        ["settings.changed"], "s3cret", max_attempts=1)
    event = _emit(platform, wid, n=1)

    first = service.dispatch_webhooks()
    assert first["failed"] == 1 and first["dead_lettered"] == 1
    dead = service.dead_letters(wid)
    assert len(dead) == 1
    delivery_id = dead[0]["id"]
    assert _event_row(platform, wid, event["event_id"])["webhook_delivered_at"]

    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    with pytest.raises(AccessDeniedError):
        service.replay_delivery(wid, "bob", delivery_id)
    with pytest.raises(AccessDeniedError):
        service.list_deliveries(wid, actor="bob")
    with pytest.raises(AccessDeniedError):
        service.dead_letters(wid, actor="bob")
    with pytest.raises(NotFoundError):
        service.replay_delivery(wid, "admin", "whd_does_not_exist")

    replayed = service.replay_delivery(wid, "admin", delivery_id)
    assert replayed["status"] == "pending"
    assert replayed["attempts"] == 0
    assert replayed["next_attempt_at"] is None
    assert replayed["last_error"] is None
    assert replayed["lease_expires_at"] is None
    assert not _event_row(platform, wid, event["event_id"]).get(
        "webhook_delivered_at"), "replay must reopen the event marker"

    state["fail"] = False
    second = service.dispatch_webhooks()
    assert second["delivered"] == 1
    delivered = service.list_deliveries(
        wid, webhook_id=hook["id"], status="delivered")
    assert len(delivered) == 1
    assert delivered[0]["attempts"] == 1
    with pytest.raises(ValidationError):
        service.replay_delivery(wid, "admin", delivery_id)


# ----------------------------------------------------------------------
# quota
# ----------------------------------------------------------------------


def test_fixed_window_quota_throttles_without_calling_transport(
        platform, empty_workspace):
    wid = empty_workspace
    clock = FakeClock()
    transport = RecordingTransport(default=200)
    service = _service(platform, transport, clock=clock)
    hook = service.create_webhook(
        wid, "admin", "https://hooks.example.test/quota",
        ["settings.changed"], "s3cret", rate_limit_per_minute=1)
    _emit(platform, wid, n=1)
    _emit(platform, wid, n=2)

    first = service.dispatch_webhooks()
    assert first["delivered"] == 1
    assert first["throttled"] == 1
    assert len(transport.calls) == 1, "throttled delivery must not hit HTTP"

    rows = service.list_deliveries(wid)
    assert sorted(row["status"] for row in rows) == ["delivered", "pending"]
    pending = next(row for row in rows if row["status"] == "pending")
    assert pending["attempts"] == 0
    assert not _event_row(platform, wid, pending["event_id"]).get(
        "webhook_delivered_at"), "throttled event must stay pending"
    stored_hook = platform.ctx.repo.get("webhook", wid, hook["id"])
    assert stored_hook["window_count"] == 1

    clock.advance(61)
    second = service.dispatch_webhooks()
    assert second["delivered"] == 1
    assert second.get("throttled") is None
    assert len(transport.calls) == 2
    stored_hook = platform.ctx.repo.get("webhook", wid, hook["id"])
    assert stored_hook["window_count"] == 1, "the window reset then counted one"
    assert all(row["status"] == "delivered"
               for row in service.list_deliveries(wid))


# ----------------------------------------------------------------------
# multiple workers: claim + lease
# ----------------------------------------------------------------------


def test_two_workers_claim_lease_prevents_duplicate_delivery(
        platform, empty_workspace):
    wid = empty_workspace
    clock = FakeClock()
    transport_a = RecordingTransport(default=200)
    transport_b = RecordingTransport(default=200)
    service_a = _service(platform, transport_a, clock=clock)
    service_b = _service(platform, transport_b, clock=clock)
    hook = service_a.create_webhook(
        wid, "admin", "https://hooks.example.test/multi",
        ["settings.changed"], "s3cret")
    event = _emit(platform, wid, n=1)

    # Simulate worker A holding the delivery lease mid-flight.
    delivery = service_a.ledger.get_or_create(wid, hook["id"], event)
    claimed = service_a.ledger.claim(
        wid, delivery["id"], service_a.worker_id, 30)
    assert claimed is not None

    blocked = service_b.dispatch_webhooks()
    assert blocked["delivered"] == 0
    assert len(transport_b.calls) == 0, "a live lease must not be double-sent"
    assert not _event_row(platform, wid, event["event_id"]).get(
        "webhook_delivered_at")

    # After the lease expires worker B takes the delivery over.
    clock.advance(31)
    taken_over = service_b.dispatch_webhooks()
    assert taken_over["delivered"] == 1
    assert len(transport_b.calls) == 1
    assert _event_row(platform, wid, event["event_id"])["webhook_delivered_at"]
    row = _delivery(service_a, wid, delivery["id"])
    assert row["status"] == "delivered"
    assert row["attempts"] == 1

    # Worker A arriving late does not re-deliver the settled pair.
    late = service_a.dispatch_webhooks()
    assert late["delivered"] == 0
    assert len(transport_a.calls) == 0


# ----------------------------------------------------------------------
# URL hardening
# ----------------------------------------------------------------------


def test_private_and_userinfo_urls_are_rejected_unless_explicitly_allowed(
        platform, empty_workspace, monkeypatch):
    wid = empty_workspace
    monkeypatch.delenv("ONTO_WEBHOOK_ALLOW_PRIVATE", raising=False)
    service = _service(platform, RecordingTransport())

    for url in ("http://127.0.0.1:8080/x",
                "http://localhost:8080/x",
                "http://10.0.0.5/x",
                "http://192.168.1.20/x",
                "http://169.254.10.1/x",
                "http://[::1]/x"):
        with pytest.raises(ValidationError):
            service.create_webhook(wid, "admin", url, ["settings.changed"])
    for url in ("https://user:pass@hooks.example.test/x",
                "ftp://hooks.example.test/x"):
        with pytest.raises(ValidationError):
            service.create_webhook(wid, "admin", url, ["settings.changed"])

    monkeypatch.setenv("ONTO_WEBHOOK_ALLOW_PRIVATE", "1")
    transport = RecordingTransport(default=200)
    service = _service(platform, transport)
    hook = service.create_webhook(
        wid, "admin", "http://127.0.0.1:8080/local",
        ["settings.changed"], "s3cret")
    assert hook["url"] == "http://127.0.0.1:8080/local"
    _emit(platform, wid, n=1)
    result = service.dispatch_webhooks()
    assert result["delivered"] == 1
    assert transport.calls[0]["url"] == "http://127.0.0.1:8080/local"
    # Userinfo is never allowed, even with the private-network escape hatch.
    with pytest.raises(ValidationError):
        service.create_webhook(
            wid, "admin", "http://user:pass@127.0.0.1:8080/x",
            ["settings.changed"])
