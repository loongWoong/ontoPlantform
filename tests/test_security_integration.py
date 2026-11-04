"""Integration contracts for the V0.4 security gaps.

These tests exercise the *composed* platform, not the individual services:
allow decisions become evidence, a multi-approver policy cannot publish early,
the org tree feeds principal attributes, the signed async context denies after
tampering/expiry, the governed repository refuses cross-tenant access, and the
new REST routes round-trip through the real handler.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from onto_platform.application.security import context as auth_context
from onto_platform.application.security.policy import default_policy_document
from onto_platform.errors import AccessDeniedError, ValidationError
from onto_platform.interfaces.api import build_handler
from tests.conftest import (
    http_call,
    http_delete,
    http_get,
    http_post,
    make_platform,
    seed_workspace,
)


def _owner(platform, workspace_id: str, user: str) -> None:
    platform.workspaces.add_member(workspace_id, user, "owner", actor="admin")


def test_allow_decisions_are_evidence_and_audit_links_decision_id(platform):
    wid = seed_workspace(platform)
    before = len(platform.ctx.security.list_decisions(wid))
    platform.ctx.security.check_action("admin", wid, "security.read")
    rows = platform.ctx.security.list_decisions(wid)
    assert len(rows) == before + 1
    decision = platform.ctx.security.check_action(
        "admin", wid, "workspace.read")
    assert decision.decision_id
    platform.ctx.audit_event("admin", wid, "doc.read", "doc-1", {"n": 1})
    entry = platform.ctx.audit.list(wid, limit=1)[0]
    assert entry["detail"]["decision_id"] == decision.decision_id


def test_multi_level_approval_needs_distinct_approvers(platform):
    wid = seed_workspace(platform)
    _owner(platform, wid, "approver-1")
    _owner(platform, wid, "approver-2")
    document = default_policy_document()
    document["approval"] = {**document.get("approval", {}),
                            "requiredApprovals": 2}
    created = platform.security.create_policy(
        wid, "admin", document=document)
    platform.security.validate_policy(wid, "admin", created["id"])

    first = platform.security.approve_policy(wid, "approver-1", created["id"])
    assert first["status"] == "validated"
    assert first["approval_progress"]["received"] == 1
    assert first["approval_progress"]["remaining"] == 1

    with pytest.raises(ValidationError):
        platform.security.approve_policy(wid, "approver-1", created["id"])
    with pytest.raises(ValidationError):
        platform.security.publish_policy(wid, "approver-1", created["id"])

    second = platform.security.approve_policy(wid, "approver-2", created["id"])
    assert second["status"] == "approved"
    assert second["approval_progress"]["satisfied"] is True
    published = platform.security.publish_policy(
        wid, "approver-2", created["id"])
    assert published["status"] == "published"


def test_editing_an_approved_policy_invalidates_existing_approvals(platform):
    wid = seed_workspace(platform)
    _owner(platform, wid, "approver-1")
    _owner(platform, wid, "approver-2")
    document = default_policy_document()
    document["approval"] = {**document.get("approval", {}),
                            "requiredApprovals": 2}
    created = platform.security.create_policy(
        wid, "admin", document=document)
    platform.security.validate_policy(wid, "admin", created["id"])
    first = platform.security.approve_policy(wid, "approver-1", created["id"])
    assert first["approval_progress"]["received"] == 1

    edited = dict(document)
    edited["metadata"] = {**edited.get("metadata", {}),
                          "name": "Edited after approval"}
    updated = platform.security.update_policy(
        wid, "admin", created["id"], edited)
    assert updated["status"] == "validated"
    assert updated["approval_progress"]["received"] == 0
    assert updated["approvals"] == []

    # One fresh approval is still not enough for the edited document.
    platform.security.approve_policy(wid, "approver-2", created["id"])
    with pytest.raises(ValidationError):
        platform.security.publish_policy(wid, "approver-2", created["id"])


def test_org_tree_populates_principal_attributes(platform):
    wid = seed_workspace(platform)
    root = platform.organization.create_unit(wid, "admin", "总部")
    child = platform.organization.create_unit(
        wid, "admin", "济南分部", parent_id=root["id"])
    platform.organization.set_members(wid, "admin", child["id"], ["bob"])
    assert platform.organization.tree(wid)["count"] == 2

    principal = platform.ctx.security.principal("bob", wid)
    assert principal.attributes["org_path"] == child["path"]
    assert principal.attributes["org_unit"] == child["id"]
    # A forged path in the caller's attributes cannot widen the real one.
    principal = platform.ctx.security.principal(
        "bob", wid, attributes={"org_path": "/forged/"})
    assert principal.attributes["org_path"] == child["path"]

    outsider = platform.ctx.security.principal("mallory", wid)
    assert "org_path" not in outsider.attributes


def test_signed_async_context_round_trip_revalidates_and_rejects_tampering(
        platform, monkeypatch):
    monkeypatch.setenv("ONTO_SECURITY_CONTEXT_SECRET", "test-secret")
    wid = seed_workspace(platform)
    platform.ctx.security.check_action("admin", wid, "security.read")
    payload = auth_context.snapshot()
    assert payload and payload["signature"]

    principal, tenant, decision = auth_context.revalidate(
        platform.ctx.security, payload)
    assert principal.id == "admin"
    assert tenant.workspace_id == wid
    assert decision.allow is True

    tampered = dict(payload)
    tampered["action"] = "security.publish"
    with pytest.raises(AccessDeniedError):
        auth_context.deserialize(tampered)

    # Removing the signature must not downgrade a signed context to an
    # unsigned one that the worker would accept.
    unsigned = {k: v for k, v in payload.items() if k != "signature"}
    with pytest.raises(AccessDeniedError):
        auth_context.deserialize(unsigned)

    # Context expiry is a deny even with a valid signature.
    expired = dict(payload)
    expired["expires_at"] = "2000-01-01T00:00:00+00:00"
    with pytest.raises(AccessDeniedError):
        auth_context.deserialize(expired)


def test_governed_repository_blocks_cross_workspace_access(platform):
    wid = seed_workspace(platform)
    other = seed_workspace(platform)
    platform.ctx.security.check_action("admin", wid, "object.read")
    with pytest.raises(AccessDeniedError):
        platform.ctx.repo.get("ontology", other, "anything")
    with pytest.raises(AccessDeniedError):
        platform.ctx.repo.put("vehicle", other, "V-X", {"plate": "X"})


def test_org_webhook_and_export_routes(http_server):
    base, wid = http_server

    status, body = http_post(
        base, f"/api/workspaces/{wid}/security/org/units",
        {"name": "总部", "user": "admin"})
    assert status == 200, body
    status, tree = http_get(
        base, f"/api/workspaces/{wid}/security/org/tree?user=admin")
    assert status == 200
    assert tree["count"] == 1

    status, hook = http_post(
        base, f"/api/workspaces/{wid}/security/webhooks",
        {"url": "https://example.test/hook", "event_types": ["todo.opened"],
         "secret": "s3cret", "user": "admin"})
    assert status == 200, hook
    status, hooks = http_get(
        base, f"/api/workspaces/{wid}/security/webhooks?user=admin")
    assert status == 200
    assert hooks[0]["secret"] == "***"

    status, body = http_get(
        base, f"/api/workspaces/{wid}/state/export?type=Vehicle&fmt=json"
              f"&user=admin")
    assert status == 200


def test_group_member_and_identity_routes(http_server):
    base, wid = http_server

    status, group = http_post(
        base, f"/api/workspaces/{wid}/security/groups",
        {"name": "operators", "members": ["alice"], "user": "admin"})
    assert status == 200, group
    status, groups = http_get(
        base, f"/api/workspaces/{wid}/security/groups?user=admin")
    assert status == 200 and groups[0]["name"] == "operators"

    status, updated = http_post(
        base, f"/api/workspaces/{wid}/security/groups/{group['id']}",
        {"members": ["alice", "bob"], "user": "admin"})
    assert status == 200, updated
    assert updated["members"] == ["alice", "bob"]

    status, user = http_post(
        base, f"/api/workspaces/{wid}/security/identity/users",
        {"user_name": "alice@example.test", "display_name": "Alice",
         "provider": "scim", "actor": "admin"})
    assert status == 200, user
    status, users = http_get(
        base, f"/api/workspaces/{wid}/security/identity/users?user=admin")
    assert status == 200
    assert users["users"][0]["user_name"] == "alice@example.test"

    status, removed = http_delete(
        base, f"/api/workspaces/{wid}/security/groups/{group['id']}")
    assert status == 200 and removed["deleted"] is True


def test_identity_directory_requires_security_manage(http_server):
    base, wid = http_server
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/members",
        {"member": "viewer1", "role": "viewer", "user": "admin"})
    assert status == 200

    status, body = http_post(
        base, f"/api/workspaces/{wid}/security/identity/users",
        {"user_name": "viewer1@example.test", "user": "viewer1"})
    assert status == 403, body
    assert "security.manage" in body.get("error", "")

    status, body = http_post(
        base, f"/api/workspaces/{wid}/security/identity/users",
        {"user_name": "outsider@example.test", "user": "admin"})
    assert status == 200, body

    status, body = http_get(
        base, f"/api/workspaces/{wid}/security/identity/users?user=viewer1")
    assert status == 200, body
    visible = {row["user_name"] for row in body["users"]}
    assert "outsider@example.test" not in visible

    status, body = http_get(
        base, f"/api/workspaces/{wid}/security/identity/users?user=admin")
    assert status == 200
    assert "outsider@example.test" in {
        row["user_name"] for row in body["users"]}


def test_policy_draft_is_not_stored_or_published(platform):
    wid = seed_workspace(platform)
    before = len(platform.security.policies(wid, "admin"))
    draft = platform.policy_drafts.draft(
        wid, "admin", "只允许 viewer 读取车辆", mode="deterministic")
    assert draft["document"]["schemaVersion"] == "1.0"
    assert draft["stored"] is False and draft["published"] is False
    assert draft["validation"]["valid"] is True
    assert len(platform.security.policies(wid, "admin")) == before


def test_deferred_action_serializes_context_and_denies_tampering(platform):
    wid = seed_workspace(platform)
    platform.start_scheduler(poll_seconds=0.05)
    try:
        queued = platform.async_tasks.enqueue(
            wid, "admin", "Vehicle.capability_that_does_not_exist",
            target="Vehicle/V001", delay_seconds=60)
        assert queued["authorization_bound"] is True
        task = next(t for t in platform.scheduler.tasks
                    if t.name == queued["id"])
        assert task.authorization and task.authorization.get("signature")

        # Editing the signed payload invalidates it: the worker must not run.
        task.authorization = {**task.authorization,
                              "action": "security.publish"}
        result = platform.async_tasks.run_now(task.name)
        assert result[0]["ok"] is False
        assert "denied" in result[0]["error"].lower() or \
               "signature" in result[0]["error"].lower()
    finally:
        platform.stop_scheduler()


def test_agent_observations_are_passed_through_the_masker():
    from onto_platform.application.agent_runner import AgentRunner

    class Gateway:
        def find_objects(self, object_type, filters=None):
            return [{"type": "Vehicle", "id": "V001",
                     "properties": {"plate": "鲁A12345"}}]

        def related(self, *args, **kwargs):
            return []

        def get_object(self, ref):
            return None

    seen: list[str] = []

    def masker(value, object_type=None):
        seen.append(object_type)
        return [{"type": "Vehicle", "id": "V001",
                 "properties": {"plate": "***"}}]

    runner = AgentRunner(Gateway(), None, "", masker=masker)
    observation = runner._execute_tool(
        {"tool": "find", "args": {"object_type": "Vehicle"}})
    assert "鲁A12345" not in observation
    assert "***" in observation
    assert seen == ["Vehicle"]


def test_legacy_role_allow_is_recorded_as_evidence(platform):
    wid = seed_workspace(platform)
    before = len(platform.ctx.security.list_decisions(wid))
    platform.ctx.require("admin", wid, "viewer")
    rows = platform.ctx.security.list_decisions(wid)
    assert len(rows) == before + 1
    recorded = next(row for row in rows if row["action"] == "role.viewer")
    assert recorded["allow"] is True
    assert recorded["decision_id"]


def test_agent_capability_observation_masker_fails_closed_without_caller(platform):
    from onto_platform.application.agent_runner import make_observation_masker

    wid = seed_workspace(platform)

    class Gateway:
        actor = None

        def execution_actor(self):
            return self.actor

    gateway = Gateway()
    masker = make_observation_masker(platform.ctx, wid, gateway)
    rows = [{"type": "Vehicle", "id": "V001",
             "properties": {"plate": "鲁A12345"}}]
    assert masker(rows, "Vehicle") == {
        "redacted": True, "reason": "no authorized caller"}

    gateway.actor = "admin"
    assert masker(rows, "Vehicle")[0]["properties"]["plate"] == "鲁A12345"


def test_session_token_authenticates_http_requests(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "alice", "viewer", actor="admin")
    platform.identity.sessions.secret = "session-test-secret"
    platform.identity_directory.upsert_user("alice", display_name="Alice")
    token = platform.identity.sessions.issue("alice")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, _, body = http_call(
            "GET", base, f"/api/workspaces/{wid}/security/roles",
            headers={"Authorization": f"Bearer {token}"})
        assert status == 200, body
        assert body["principal"]["id"] == "alice"
        assert body["can_manage"] is False

        # A bad bearer token never falls back to local admin mode.
        status, _, body = http_call(
            "GET", base, f"/api/workspaces/{wid}/security/roles",
            headers={"Authorization": "Bearer not-a-session"})
        assert status == 403, body

        # A valid session with no membership cannot read the tenant's audit,
        # event feed or change log (those routes used to skip the PEP).
        platform.identity_directory.upsert_user("mallory")
        outsider = platform.identity.sessions.issue("mallory")
        for path in ("changes", "events", "audit"):
            status, _, body = http_call(
                "GET", base, f"/api/workspaces/{wid}/{path}",
                headers={"Authorization": f"Bearer {outsider}"})
            assert status == 403, (path, body)
    finally:
        httpd.shutdown()


def test_scim_http_route_fails_closed_without_configured_token(platform):
    # No ONTO_SCIM_BEARER_TOKEN in the test environment: the endpoint must
    # reject anonymous provisioning instead of silently opening SCIM.
    platform.scim._bearer_token = ""
    platform.scim._allow_anonymous = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, body = http_get(base, "/scim/v2/Users")
        assert status == 403, body
    finally:
        httpd.shutdown()


def test_scheduler_does_not_leak_authorization_context_between_tasks(platform):
    from onto_platform.application.scheduler import Scheduler

    wid = seed_workspace(platform)
    scheduler = Scheduler(platform.ctx)
    scheduler.every(
        "bind", 0,
        lambda: platform.ctx.security.check_role("admin", wid, "viewer"))
    seen: list[dict | None] = []
    scheduler.every("observe", 0,
                    lambda: seen.append(auth_context.current()))

    scheduler.run_once("bind")
    scheduler.run_once("observe")
    # The system task must not inherit the role check's ALLOW (and its row
    # scope) from the previous task on the same scheduler thread.
    assert seen == [None]


def test_webhook_dispatch_survives_outbox_dispatch_and_is_scheduled(platform):
    wid = seed_workspace(platform)
    calls: list[dict] = []

    def transport(url, body, headers, timeout):
        calls.append({"url": url, "body": body, "headers": headers})
        return 200

    platform.webhooks.transport = transport
    platform.webhooks.create_webhook(
        wid, "admin", "https://hooks.example.test/onto",
        ["workspace.created"], "s3cret")
    platform.events.emit("workspace.created", wid, "admin",
                         "workspace", wid, {"n": 1})

    # The in-process outbox consumes the event first and stamps dispatched_at.
    platform.dispatcher.dispatch()
    event = platform.ctx.repo.list("event", wid)[0]
    assert event.get("dispatched_at")

    # Webhook delivery is independent of that transactional marker.
    result = platform.webhooks.dispatch_webhooks()
    assert result["delivered"] == 1
    assert len(calls) == 1

    platform.start_scheduler(poll_seconds=30)
    try:
        names = [task.name for task in platform.scheduler.tasks]
        assert "webhooks.deliver" in names
    finally:
        platform.stop_scheduler()

