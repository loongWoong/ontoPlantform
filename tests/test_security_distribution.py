"""Distribution tests: authorized export + signed/masked webhooks.

The tests pin the design §4.4/§8.3 contract:

* exports go through the real PEP, so masked fields never reach JSON/CSV and
  the caller's input rows are never mutated;
* webhook CRUD is an owner action, URL/event types are validated, payloads are
  masked recursively before leaving the platform, and requests carry an HMAC
  signature;
* delivery failures are recorded on the webhook and retried, while the
  transactional-outbox fields on the event stay untouched.

No socket is opened: the webhook transport is injected.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from copy import deepcopy

import pytest

from onto_platform.application.security.distribution import (
    ExportService,
    WebhookService,
)
from onto_platform.application.security.policy import default_policy_document
from onto_platform.errors import AccessDeniedError, NotFoundError, ValidationError
from tests.conftest import make_platform

ID_NUMBER = "110101199001011234"
PHONE = "13800138000"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _masked_policy_document() -> dict:
    """Built-in policy + field masks on the wildcard object.read rule."""
    document = default_policy_document()
    for rule in document["actionPolicies"]:
        if rule["action"] == "object.read" and rule["resource"] == "*":
            rule["fieldMasks"] = [
                {"field": "id_number", "mask": "id_number"},
                {"field": "phone", "mask": "phone"},
            ]
    return document


def _publish(platform, workspace_id: str, document: dict) -> dict:
    platform.ctx.repo.put("membership", workspace_id, "approver", {
        "workspace_id": workspace_id, "user": "approver", "role": "owner"})
    created = platform.security.create_policy(
        workspace_id, "admin", document=document)
    platform.security.validate_policy(workspace_id, "admin", created["id"])
    platform.security.approve_policy(
        workspace_id, "approver", created["id"])
    return platform.security.publish_policy(
        workspace_id, "approver", created["id"])


def _workspace(platform) -> str:
    wid = platform.workspaces.create("distribution", actor="admin")["id"]
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    _publish(platform, wid, _masked_policy_document())
    return wid


def _objects() -> list[dict]:
    return [
        {
            "ref": "Vehicle/V1",
            "type": "Vehicle",
            "properties": {
                "id_number": ID_NUMBER,
                "phone": PHONE,
                "plate": "A12345",
            },
            "meta": {"row": 1},
        },
        {
            "ref": "Vehicle/V2",
            "type": "Vehicle",
            "properties": {
                "id_number": "320102198505054321",
                "phone": "13912345678",
                "plate": "B99999",
            },
        },
    ]


def _emit(platform, workspace_id: str, actor: str = "bob",
          payload: dict | None = None, event_type: str = "workspace.member_added"
          ) -> dict:
    return platform.events.emit(
        event_type, workspace_id, actor, "workspace", workspace_id,
        payload or {})


class FakeTransport:
    """Injected transport: records requests, returns/raises scripted outcomes."""

    def __init__(self, statuses: list | None = None,
                 error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.statuses = list(statuses or [])
        self.error = error

    def __call__(self, url: str, body: bytes, headers: dict,
                 timeout: float) -> int:
        self.calls.append({
            "url": url, "body": body, "headers": dict(headers),
            "timeout": timeout,
        })
        if self.error is not None:
            raise self.error
        if self.statuses:
            return self.statuses.pop(0)
        return 200


# ---------------------------------------------------------------------------
# object export
# ---------------------------------------------------------------------------


def test_export_objects_json_masks_and_preserves_input():
    platform = make_platform()
    wid = _workspace(platform)
    objects = _objects()
    snapshot = deepcopy(objects)

    service = ExportService(platform.ctx, platform.security)
    payload = service.export_objects(wid, "admin", objects)

    assert payload["content_type"] == "application/json"
    assert payload["filename"] == "objects.json"
    body = payload["body"]
    assert isinstance(body, bytes)
    text = body.decode("utf-8")
    assert ID_NUMBER not in text
    assert PHONE not in text
    rows = json.loads(body)
    assert len(rows) == 2
    assert rows[0]["properties"]["id_number"] == "110********1234"
    assert rows[0]["properties"]["phone"] == "138****8000"
    assert rows[0]["properties"]["plate"] == "A12345"
    # The caller's rows are never modified.
    assert objects == snapshot


def test_export_objects_csv_serializes_nested_values_and_bom():
    platform = make_platform()
    wid = _workspace(platform)
    objects = _objects()
    snapshot = deepcopy(objects)

    service = ExportService(platform.ctx, platform.security)
    payload = service.export_objects(wid, "admin", objects, fmt="csv")

    assert payload["filename"] == "objects.csv"
    assert payload["content_type"].startswith("text/csv")
    text = payload["body"].decode("utf-8")
    assert ID_NUMBER not in text
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    assert len(rows) == 2
    props = json.loads(rows[0]["properties"])
    assert props["id_number"] == "110********1234"
    assert props["phone"] == "138****8000"
    # nested objects are JSON-encoded rather than stringified with Python repr
    assert json.loads(rows[0]["meta"]) == {"row": 1}
    assert objects == snapshot

    with_bom = service.export_objects(wid, "admin", objects, fmt="csv", bom=True)
    assert with_bom["body"].startswith(b"\xef\xbb\xbf")
    assert ID_NUMBER.encode() not in with_bom["body"]


def test_export_objects_empty_result_is_not_an_error():
    platform = make_platform()
    wid = _workspace(platform)
    service = ExportService(platform.ctx, platform.security)

    json_payload = service.export_objects(wid, "admin", [])
    assert json.loads(json_payload["body"]) == []

    csv_payload = service.export_objects(wid, "admin", [], fmt="csv")
    assert csv_payload["body"] == b""


def test_export_masks_flat_rows_with_top_level_business_fields():
    platform = make_platform()
    wid = _workspace(platform)
    objects = [{"ref": "Vehicle/V1", "id_number": ID_NUMBER, "phone": PHONE}]
    snapshot = deepcopy(objects)

    service = ExportService(platform.ctx, platform.security)
    payload = service.export_objects(wid, "admin", objects)
    text = payload["body"].decode("utf-8")
    rows = json.loads(payload["body"])
    assert rows[0]["id_number"] == "110********1234"
    assert rows[0]["phone"] == "138****8000"
    assert ID_NUMBER not in text
    assert PHONE not in text
    assert objects == snapshot


def test_export_uses_kernel_fallback_when_facade_is_absent():
    """``ctx.security`` is the kernel, not the use-case facade."""
    platform = make_platform()
    wid = _workspace(platform)
    objects = _objects()
    snapshot = deepcopy(objects)

    service = ExportService(platform.ctx)
    payload = service.export_objects(wid, "admin", objects)
    text = payload["body"].decode("utf-8")
    assert ID_NUMBER not in text
    assert PHONE not in text
    assert objects == snapshot


def test_export_rejects_unknown_format_and_outsider():
    platform = make_platform()
    wid = _workspace(platform)
    service = ExportService(platform.ctx, platform.security)

    with pytest.raises(ValidationError):
        service.export_objects(wid, "admin", [], fmt="xml")
    with pytest.raises(AccessDeniedError):
        service.export_objects(wid, "outsider", _objects())


# ---------------------------------------------------------------------------
# decision export
# ---------------------------------------------------------------------------


def test_export_decisions_requires_security_read_and_is_jsonl():
    platform = make_platform()
    wid = _workspace(platform)
    decision = platform.security.authorize(
        wid, "admin", "workspace.read")["decision"]

    service = ExportService(platform.ctx, platform.security)
    payload = service.export_decisions(wid, "admin")
    assert payload["content_type"].startswith("application/x-ndjson")
    assert payload["filename"] == "decisions.jsonl"
    rows = [json.loads(line) for line in
            payload["body"].decode("utf-8").splitlines() if line]
    assert any(row["decision_id"] == decision["decision_id"] for row in rows)

    # viewer-level security.read is enough for evidence export: the same
    # workspace decision list is returned (evidence is not field-masked) ...
    bob_rows = [json.loads(line) for line in
                service.export_decisions(wid, "bob")["body"]
                .decode("utf-8").splitlines() if line]
    assert any(row["decision_id"] == decision["decision_id"]
               for row in bob_rows)
    # ... but a non-member is denied.
    with pytest.raises(AccessDeniedError):
        service.export_decisions(wid, "outsider")


# ---------------------------------------------------------------------------
# webhook CRUD / validation
# ---------------------------------------------------------------------------


def test_webhook_crud_requires_management_permission():
    platform = make_platform()
    wid = _workspace(platform)
    service = WebhookService(platform.ctx, platform.security)

    with pytest.raises(AccessDeniedError):
        service.create_webhook(wid, "bob", "https://hooks.example.test/x",
                               ["workspace.created"], "s")
    hook = service.create_webhook(
        wid, "admin", "https://hooks.example.test/x",
        ["workspace.created"], "s3cret")
    assert hook["active"] is True
    assert hook["delivery_count"] == 0
    assert hook["event_types"] == ["workspace.created"]

    # listing with an actor requires security.manage (rows carry the secret)
    assert [item["id"] for item in service.list_webhooks(wid)] == [hook["id"]]
    with pytest.raises(AccessDeniedError):
        service.list_webhooks(wid, actor="bob")

    with pytest.raises(AccessDeniedError):
        service.update_webhook(wid, "bob", hook["id"], active=False)
    updated = service.update_webhook(
        wid, "admin", hook["id"], active=False,
        event_types=["workspace.created", "workspace.member_added"])
    assert updated["active"] is False
    assert updated["event_types"] == ["workspace.created",
                                      "workspace.member_added"]

    with pytest.raises(AccessDeniedError):
        service.delete_webhook(wid, "bob", hook["id"])
    assert service.delete_webhook(wid, "admin", hook["id"])["deleted"] is True
    assert service.list_webhooks(wid) == []
    with pytest.raises(NotFoundError):
        service.delete_webhook(wid, "admin", hook["id"])


def test_webhook_rejects_bad_url_and_unknown_event_type():
    platform = make_platform()
    wid = _workspace(platform)
    service = WebhookService(platform.ctx, platform.security)

    for url in ("ftp://hooks.example.test/x", "/relative", "",
                "https:///missing-host"):
        with pytest.raises(ValidationError):
            service.create_webhook(wid, "admin", url, ["workspace.created"])
    for types in ([], ["no.such.event"], "workspace.created", [1]):
        with pytest.raises(ValidationError):
            service.create_webhook(wid, "admin",
                                   "https://hooks.example.test/x", types)

    hook = service.create_webhook(wid, "admin", "https://hooks.example.test/x",
                                  ["workspace.created"])
    with pytest.raises(ValidationError):
        service.update_webhook(wid, "admin", hook["id"],
                               url="ftp://hooks.example.test/x")
    with pytest.raises(ValidationError):
        service.update_webhook(wid, "admin", hook["id"],
                               event_types=["no.such.event"])


# ---------------------------------------------------------------------------
# webhook delivery / masking / signing
# ---------------------------------------------------------------------------


def test_dispatch_signs_and_masks_payload_recursively():
    platform = make_platform()
    wid = _workspace(platform)
    transport = FakeTransport()
    service = WebhookService(platform.ctx, platform.security,
                             transport=transport)
    hook = service.create_webhook(
        wid, "admin", "https://hooks.example.test/onto",
        ["workspace.member_added"], "s3cret")
    event = _emit(platform, wid, payload={
        "id_number": ID_NUMBER,
        "phone": PHONE,
        "nested": {"phone": "13912345678",
                   "id_number": "320102198505054321"},
        "rows": [{"phone": PHONE}],
        "note": "keep-me",
    })

    result = service.dispatch_webhooks()
    assert result == {"delivered": 1, "failed": 0, "skipped": 0,
                      "errors": []}
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://hooks.example.test/onto"
    assert call["timeout"] == 5.0
    headers = call["headers"]
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Onto-Event"] == "workspace.member_added"
    assert headers["X-Onto-Event-Id"] == event["event_id"]
    expected = "sha256=" + hmac.new(
        b"s3cret", call["body"], hashlib.sha256).hexdigest()
    assert headers["X-Onto-Signature"] == expected

    body = json.loads(call["body"])
    payload = body["payload"]
    assert payload["id_number"] == "110********1234"
    assert payload["phone"] == "138****8000"
    assert payload["nested"]["phone"] == "139****5678"
    assert payload["nested"]["id_number"] == "320********4321"
    assert payload["rows"] == [{"phone": "138****8000"}]
    assert payload["note"] == "keep-me"
    raw = call["body"].decode("utf-8")
    assert ID_NUMBER not in raw
    assert PHONE not in raw

    stored = service.list_webhooks(wid)[0]
    assert stored["delivery_count"] == 1
    assert stored["last_delivery_at"]
    assert stored["last_error"] is None
    assert stored["id"] == hook["id"]

    # The transactional outbox fields are untouched; only the independent
    # webhook marker records that this receiver already accepted the event.
    stored_event = next(
        row for row in platform.ctx.repo.list("event", wid)
        if row["event_id"] == event["event_id"])
    assert stored_event["dispatched_at"] is None
    assert stored_event["attempts"] == 0
    assert stored_event["webhook_delivered_at"]
    assert stored_event["payload"]["id_number"] == ID_NUMBER  # stored event intact

    # A second pass does not duplicate the delivery.
    assert service.dispatch_webhooks() == {
        "delivered": 0, "failed": 0, "skipped": 0, "errors": []}
    assert len(transport.calls) == 1


def test_mask_event_fails_closed_and_never_mutates():
    platform = make_platform()
    wid = _workspace(platform)
    service = WebhookService(platform.ctx, platform.security)
    event = _emit(platform, wid, payload={
        "id_number": ID_NUMBER, "phone": PHONE,
        "nested": {"phone": PHONE},
    })
    snapshot = deepcopy(event)

    masked = service.mask_event(wid, "bob", event)
    assert masked["payload"]["id_number"] == "110********1234"
    assert masked["payload"]["nested"]["phone"] == "138****8000"
    assert event == snapshot  # deep copy, not in-place masking

    minimal = service.mask_event(wid, "outsider", event)
    assert minimal == {"event_id": event["event_id"],
                       "type": event["type"]}
    assert minimal == service.mask_event(wid, "ghost", event)


def test_dispatch_failure_is_recorded_and_retried():
    platform = make_platform()
    wid = _workspace(platform)
    transport = FakeTransport(statuses=[500])
    service = WebhookService(platform.ctx, platform.security,
                             transport=transport)
    service.create_webhook(wid, "admin", "https://hooks.example.test/fail",
                           ["workspace.created"], "s3cret")
    _emit(platform, wid, event_type="workspace.created",
          payload={"phone": PHONE})

    first = service.dispatch_webhooks()
    assert first["delivered"] == 0
    assert first["failed"] == 1
    assert first["skipped"] == 0
    assert "500" in first["errors"][0]

    stored = service.list_webhooks(wid)[0]
    assert stored["delivery_count"] == 0
    assert "500" in stored["last_error"]
    assert stored["last_delivery_at"] is None
    event_row = platform.ctx.repo.list("event", wid)[0]
    # not marked as delivered, so the retry still sees it
    assert not event_row.get("webhook_delivered_at")

    # transport script exhausted -> next call succeeds
    second = service.dispatch_webhooks()
    assert second["delivered"] == 1
    assert second["failed"] == 0
    assert second["errors"] == []
    stored = service.list_webhooks(wid)[0]
    assert stored["delivery_count"] == 1
    assert stored["last_error"] is None
    assert stored["last_delivery_at"]

    actions = [row["action"] for row in platform.ctx.audit.list(wid)]
    assert "webhook.delivery_failed" in actions
    assert "webhook.delivered" in actions


def test_dispatch_fails_closed_for_actor_without_read_access():
    """A non-member emitter yields the minimal envelope, never the payload."""
    platform = make_platform()
    wid = _workspace(platform)
    transport = FakeTransport()
    service = WebhookService(platform.ctx, platform.security,
                             transport=transport)
    service.create_webhook(wid, "admin", "https://hooks.example.test/x",
                           ["workspace.created"], "s3cret")
    event = _emit(platform, wid, actor="intruder",
                  event_type="workspace.created", payload={"phone": PHONE})

    assert service.dispatch_webhooks()["delivered"] == 1
    body = transport.calls[0]["body"]
    assert json.loads(body) == {"event_id": event["event_id"],
                                "type": "workspace.created"}
    assert PHONE.encode() not in body


def test_dispatch_skips_unmatched_events_without_marking_them():
    platform = make_platform()
    wid = _workspace(platform)
    transport = FakeTransport()
    service = WebhookService(platform.ctx, platform.security,
                             transport=transport)
    service.create_webhook(wid, "admin", "https://hooks.example.test/x",
                           ["workspace.created"], "s3cret")
    inactive = service.create_webhook(
        wid, "admin", "https://hooks.example.test/off",
        ["workspace.member_added"], "s3cret")
    service.update_webhook(wid, "admin", inactive["id"], active=False)
    event = _emit(platform, wid, event_type="workspace.member_added",
                  payload={"phone": PHONE})

    result = service.dispatch_webhooks()
    assert result == {"delivered": 0, "failed": 0, "skipped": 1,
                      "errors": []}
    assert transport.calls == []
    stored = next(row for row in platform.ctx.repo.list("event", wid)
                  if row["event_id"] == event["event_id"])
    assert not stored.get("webhook_delivered_at")
    assert stored["dispatched_at"] is None
