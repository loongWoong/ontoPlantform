"""P2-A regression: repository-level tenancy and business-event webhooks.

Two capabilities existed before P2 but were only covered indirectly:

* ``GovernedRepository`` is installed as ``ctx.repo`` and pins every active
  request to its decision's workspace.  The test below exercises the real
  installed boundary, not a hand-built scoped wrapper.
* ``WebhookService`` can deliver any outbox event, but its allow-list used to
  reject P1 business-event contracts.  The test creates a published contract,
  subscribes a webhook to it and proves the signed delivery + idempotent marker.
"""

from __future__ import annotations

import json

import pytest

from onto_platform.application.security import context as auth_context
from onto_platform.domain.security.model import Resource
from onto_platform.errors import AccessDeniedError, ValidationError
from tests.conftest import seed_workspace


def _activated_admin(platform, workspace_id):
    principal = platform.ctx.security.principal("admin", workspace_id)
    tenant = platform.ctx.security.tenant_context(workspace_id)
    decision = platform.ctx.security.decision_for(
        principal, tenant, "object.read",
        Resource(type="Vehicle", workspace_id=workspace_id), check_scope=False)
    return auth_context.activated(
        auth_context.serialize(principal, tenant, decision))


# ----------------------------------------------------------------------
# tenant boundary
# ----------------------------------------------------------------------


def test_installed_repository_denies_cross_workspace_access(platform):
    wid = seed_workspace(platform)
    other = seed_workspace(platform)
    platform.ctx.repo.raw.put("vehicle", wid, "V1", {"plate": "A"})
    platform.ctx.repo.raw.put("vehicle", other, "V2", {"plate": "B"})

    with _activated_admin(platform, wid):
        # Reads cannot cross the pinned tenant, even for an admin principal.
        with pytest.raises(AccessDeniedError):
            platform.ctx.repo.get("vehicle", other, "V2")
        with pytest.raises(AccessDeniedError):
            platform.ctx.repo.list("vehicle", other)
        # Writes/deletes are pinned before the storage call.
        with pytest.raises(AccessDeniedError):
            platform.ctx.repo.put("vehicle", other, "V3", {"plate": "C"})
        with pytest.raises(AccessDeniedError):
            platform.ctx.repo.delete("vehicle", other, "V2")

        # A payload cannot spoof workspace/tenant; the boundary overwrites it.
        platform.ctx.repo.put("vehicle", wid, "V9", {
            "plate": "D", "workspace_id": other, "tenant_id": "evil"})
        stored = platform.ctx.repo.get("vehicle", wid, "V9")
        assert stored["workspace_id"] == wid
        assert stored["tenant_id"] == platform.ctx.security.tenant_context(
            wid).tenant_id


# ----------------------------------------------------------------------
# business event -> signed webhook
# ----------------------------------------------------------------------


def test_published_business_event_is_delivered_to_a_webhook(
        platform, workspace_id):
    calls: list[dict] = []

    def transport(url, body, headers, timeout):
        calls.append({"url": url, "body": body, "headers": headers})
        return 200

    platform.webhooks.transport = transport
    platform.business_events.create_contract(
        workspace_id, "admin", "traffic.vehicle_ping", "Vehicle",
        {"vid": {"type": "string"}, "speed": {"type": "number"}})
    platform.business_events.publish_contract(
        workspace_id, "admin", "traffic.vehicle_ping")
    platform.webhooks.create_webhook(
        workspace_id, "admin", "https://hooks.example.test/business",
        ["traffic.vehicle_ping"], "s3cret")

    event = platform.business_events.emit(
        workspace_id, "admin", "traffic.vehicle_ping", "Vehicle", "V001",
        {"vid": "V001", "speed": 88})
    result = platform.webhooks.dispatch_webhooks()

    assert result["delivered"] == 1
    assert result["failed"] == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://hooks.example.test/business"
    assert call["headers"]["X-Onto-Event"] == "traffic.vehicle_ping"
    assert call["headers"]["X-Onto-Event-Id"] == event["event_id"]
    body = json.loads(call["body"])
    assert body["type"] == "traffic.vehicle_ping"
    assert body["payload"] == {"vid": "V001", "speed": 88}

    stored = next(
        row for row in platform.ctx.repo.list("event", workspace_id)
        if row.get("event_id") == event["event_id"])
    assert stored["webhook_delivered_at"]

    # second pass is idempotent
    assert platform.webhooks.dispatch_webhooks()["delivered"] == 0
    assert len(calls) == 1


def test_webhook_rejects_unpublished_business_contract(platform, workspace_id):
    platform.business_events.create_contract(
        workspace_id, "admin", "traffic.draft_event", "Vehicle",
        {"vid": {"type": "string"}})

    with pytest.raises(ValidationError, match="not published"):
        platform.webhooks.create_webhook(
            workspace_id, "admin", "https://hooks.example.test/draft",
            ["traffic.draft_event"], "s3cret")
