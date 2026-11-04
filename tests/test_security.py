"""Authorization kernel tests (design §2-§7).

These tests pin the deterministic properties the permission design promises:
deny-by-default, tenant isolation, role inheritance, ReBAC, row scope, field
masks, policy lifecycle with separation of duties, Blueprint security
validation, and the opt-in scoped repository.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from onto_platform.application.security.policy import (
    default_policy_document,
    policy_validation_report,
)
from onto_platform.domain.governance.model import AccessPolicy
from onto_platform.domain.security.filters import normalize_filter
from onto_platform.errors import (
    AccessDeniedError,
    ConcurrencyConflictError,
    ValidationError,
)
from onto_platform.infrastructure.persistence import ScopedRepository
from tests.conftest import (
    http_delete,
    http_get,
    http_post,
    http_put,
    make_platform,
    seed_workspace,
)


def _owners_who_can_publish(platform, workspace_id: str,
                            *users: str) -> None:
    for user in users:
        platform.ctx.repo.put("membership", workspace_id, user, {
            "workspace_id": workspace_id, "user": user, "role": "owner"})


def _publish(platform, workspace_id: str, document: dict, *,
             author: str = "admin", approver: str = "approver") -> dict:
    """Create → validate → approve → publish, returning the published record."""
    _owners_who_can_publish(platform, workspace_id, approver)
    created = platform.security.create_policy(
        workspace_id, author, document=document)
    platform.security.validate_policy(workspace_id, author, created["id"])
    platform.security.approve_policy(workspace_id, approver, created["id"])
    return platform.security.publish_policy(
        workspace_id, approver, created["id"])


def _viewer_document() -> dict:
    return default_policy_document()


# ---------------------------------------------------------------------------
# policy data and validation
# ---------------------------------------------------------------------------


def test_default_policy_mirrors_legacy_matrix():
    document = default_policy_document()
    report = policy_validation_report(document)
    assert report["valid"] is True
    assert report["errors"] == []
    actions = {rule["action"] for rule in document["actionPolicies"]}
    # Every legacy action remains represented; the new read/invoke actions are
    # additive.  This is the compatibility contract for V0.1.
    assert set(AccessPolicy.ACTION_POLICY) <= actions
    assert {"object.read", "capability.invoke",
            "security.read", "security.manage", "security.publish"} <= actions
    roles = {role["id"] for role in document["roles"]}
    assert {"viewer", "editor", "owner"} <= roles


def test_policy_validation_rejects_unscoped_and_unsafe_values():
    document = _viewer_document()
    document["scopes"]["bad"] = {"filter": {"field": "station_id",
                                            "op": "eq", "value": "S1"}}
    report = policy_validation_report(document)
    assert report["valid"] is False
    assert any("tenant" in error or "workspace" in error
               for error in report["errors"])

    with pytest.raises(ValidationError):
        normalize_filter({"sql": "1=1"}, require_tenant=True)


def test_policy_validation_rejects_unknown_role_and_cycle():
    document = _viewer_document()
    document["roles"].append({"id": "ghost", "inherits": ["missing"]})
    report = policy_validation_report(document)
    assert report["valid"] is False
    assert any("missing" in error for error in report["errors"])

    cyclic = _viewer_document()
    cyclic["roles"].append({"id": "a", "inherits": ["b"]})
    cyclic["roles"].append({"id": "b", "inherits": ["a"]})
    report = policy_validation_report(cyclic)
    assert report["valid"] is False
    assert any("cycle" in error.lower() for error in report["errors"])


# ---------------------------------------------------------------------------
# PEP / PDP decision order
# ---------------------------------------------------------------------------


def test_decision_denies_unknown_action_and_outsider(platform):
    wid = seed_workspace(platform)
    decision = platform.ctx.security.check_action(
        "admin", wid, "workspace.read")
    assert decision.allow is True
    with pytest.raises(AccessDeniedError):
        platform.ctx.security.check_action("admin", wid, "no.such.action")
    with pytest.raises(AccessDeniedError):
        platform.ctx.security.check_action("outsider", wid, "workspace.read")


def test_platform_admin_is_auditor_but_not_action_bypass():
    platform = make_platform(extra={"platform_admins": ("auditor",)})
    wid = seed_workspace(platform)
    # read-only viewer exception still works
    platform.ctx.require("auditor", wid, "viewer")
    # action checks are not bypassed for platform admins
    with pytest.raises(AccessDeniedError):
        platform.ctx.security.check_action("auditor", wid,
                                             "workspace.manage_members")


def test_tenant_mismatch_denies_even_with_matching_role():
    platform = make_platform()
    wid = seed_workspace(platform)
    other = seed_workspace(platform)
    tenant = platform.ctx.security.tenant_context(wid)
    from onto_platform.domain.security.model import Resource

    resource = Resource(type="workspace", id=wid,
                        tenant_id=tenant.tenant_id,
                        workspace_id=other)
    principal = platform.ctx.security.principal("admin", wid)
    decision = platform.ctx.security.decision_for(
        principal, tenant, "workspace.read", resource)
    assert decision.allow is False
    assert decision.reason == "resource_workspace_mismatch"


def test_cross_workspace_membership_is_denied(platform):
    wid = seed_workspace(platform)
    other = seed_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    assert platform.ctx.security.check_action("bob", wid, "workspace.read")
    with pytest.raises(AccessDeniedError):
        platform.ctx.security.check_action("bob", other, "workspace.read")


# ---------------------------------------------------------------------------
# lifecycle and separation of duties
# ---------------------------------------------------------------------------


def test_policy_lifecycle_publish_rollback_and_immutability(platform):
    wid = seed_workspace(platform)
    _owners_who_can_publish(platform, wid, "approver")
    created = platform.security.create_policy(wid, "admin")
    assert created["status"] == "draft"

    validated = platform.security.validate_policy(wid, "admin", created["id"])
    assert validated["validation"]["valid"] is True
    assert validated["status"] == "validated"

    with pytest.raises(ValidationError):
        platform.security.approve_policy(wid, "admin", created["id"])

    approved = platform.security.approve_policy(wid, "approver", created["id"])
    assert approved["status"] == "approved"
    published = platform.security.publish_policy(wid, "approver",
                                                  created["id"])
    assert published["status"] == "published"
    assert published["version"] == 1

    policies = platform.security.policies(wid, "admin")
    assert policies["active"]["id"] == created["id"]
    # A published version can no longer be edited.
    with pytest.raises(ValidationError):
        platform.security.update_policy(wid, "admin", created["id"], {})

    rolled = platform.security.rollback_policy(wid, "admin", created["id"])
    assert rolled["status"] == "published"
    assert rolled["version"] == 2
    assert rolled["parent_id"] == created["id"]


def test_dry_run_uses_same_pdp(platform):
    wid = seed_workspace(platform)
    created = platform.security.create_policy(wid, "admin")
    result = platform.security.dry_run(wid, "admin", created["id"],
                                       action="workspace.read")
    assert result["decision"]["allow"] is True
    denied = platform.security.dry_run(wid, "admin", created["id"],
                                       action="no.such.action")
    assert denied["decision"]["allow"] is False
    assert denied["decision"]["reason"] == "no_matching_action_policy"


def test_dry_run_can_evaluate_another_subject_without_impersonation(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    created = platform.security.create_policy(wid, "admin")

    as_bob = platform.security.dry_run(
        wid, "admin", created["id"], action="workspace.manage_members",
        subject="bob")
    assert as_bob["subject"] == "bob"
    assert as_bob["decision"]["allow"] is False
    # The administrator's own identity is unchanged by the preview.
    assert platform.security.dry_run(
        wid, "admin", created["id"],
        action="workspace.manage_members")["subject"] == "admin"


def test_policy_update_rejects_stale_expected_version(platform):
    wid = seed_workspace(platform)
    created = platform.security.create_policy(wid, "admin")
    stale = int(created["version"]) + 1
    with pytest.raises(ConcurrencyConflictError):
        platform.security.update_policy(
            wid, "admin", created["id"], created["document"],
            expected_version=stale)
    updated = platform.security.update_policy(
        wid, "admin", created["id"], created["document"],
        expected_version=int(created["version"]))
    assert updated["status"] in {"draft", "validated"}
    assert updated["document"] == created["document"]


# ---------------------------------------------------------------------------
# ReBAC
# ---------------------------------------------------------------------------


def test_relation_tuple_grants_only_named_resource(platform):
    wid = seed_workspace(platform)
    document = _viewer_document()
    document["roles"].append({"id": "doc_reader", "inherits": ["viewer"]})
    document["actionPolicies"].append({
        "action": "document.read", "resource": "Document",
        "allowRoles": ["doc_reader"], "relation": "viewer",
        "scope": "workspace_scope",
    })
    _publish(platform, wid, document)
    platform.ctx.security.create_binding(
        wid, "admin", subject_type="user", subject_id="bob",
        role="doc_reader")
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    platform.ctx.security.create_relation(
        wid, "admin", resource_type="Document", resource_id="D1",
        relation="viewer", subject_type="user", subject_id="bob")

    tenant = platform.ctx.security.tenant_context(wid)
    principal = platform.ctx.security.principal("bob", wid)
    from onto_platform.domain.security.model import Resource

    allowed = platform.ctx.security.decision_for(
        principal, tenant, "document.read",
        Resource(type="Document", id="D1", workspace_id=wid))
    denied = platform.ctx.security.decision_for(
        principal, tenant, "document.read",
        Resource(type="Document", id="D2", workspace_id=wid))
    assert allowed.allow is True
    assert denied.allow is False
    assert denied.reason == "relation_denied"


# ---------------------------------------------------------------------------
# Explicit deny overrides allow (design §3.3 / §6.4)
# ---------------------------------------------------------------------------


def test_explicit_deny_roles_override_allow(platform):
    wid = seed_workspace(platform)
    _owners_who_can_publish(platform, wid, "approver")
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    document = default_policy_document(AccessPolicy)
    document["roles"].append({"id": "suspended", "inherits": []})
    for rule in document["actionPolicies"]:
        if rule["action"] == "workspace.read":
            rule["denyRoles"] = ["suspended"]
    _publish(platform, wid, document, approver="approver")
    platform.ctx.security.create_binding(
        wid, "admin", subject_type="user", subject_id="bob",
        role="suspended")
    tenant = platform.ctx.security.tenant_context(wid)
    from onto_platform.domain.security.model import Resource

    bob = platform.ctx.security.principal("bob", wid)
    admin = platform.ctx.security.principal("admin", wid)
    denied = platform.ctx.security.decision_for(
        bob, tenant, "workspace.read",
        Resource(type="workspace", id=wid, workspace_id=wid))
    allowed = platform.ctx.security.decision_for(
        admin, tenant, "workspace.read",
        Resource(type="workspace", id=wid, workspace_id=wid))
    assert denied.allow is False
    assert denied.reason == "explicit_deny"
    assert allowed.allow is True


# ---------------------------------------------------------------------------
# ABAC row scope + field masks through the real PEP
# ---------------------------------------------------------------------------


def _scoped_vehicle_document() -> dict:
    document = _viewer_document()
    document["roles"].append({"id": "station_manager",
                              "inherits": ["viewer"]})
    document["scopes"]["station_self"] = {"filter": {"all": [
        {"field": "tenant_id", "op": "eq",
         "valueFrom": "context.tenant_id"},
        {"field": "station_id", "op": "eq",
         "valueFrom": "principal.attributes.station_id"},
    ]}}
    for rule in document["actionPolicies"]:
        if rule["action"] == "object.read" and rule["resource"] == "*":
            rule.update({
                "resource": "Vehicle",
                "allowRoles": ["station_manager"],
                "scope": "station_self",
                "fieldMasks": [{"field": "plate", "mask": "plate"}],
            })
    return document


def test_ask_answer_is_composed_only_from_masked_rows(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    document = _viewer_document()
    for rule in document["actionPolicies"]:
        if rule["action"] == "object.read":
            rule["fieldMasks"] = [{"field": "plate", "mask": "plate"}]
    _publish(platform, wid, document, approver="approver")

    result = platform.intelligence.ask(wid, "bob", "车辆")
    assert "鲁A12345" not in result["answer"]
    assert "***" in result["answer"]
    assert result["objects"]
    assert result["objects"][0]["properties"]["plate"] != "鲁A12345"


def test_row_scope_and_field_mask_apply_at_query_boundary(platform):
    wid = seed_workspace(platform)
    _owners_who_can_publish(platform, wid, "approver")
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    _publish(platform, wid, _scoped_vehicle_document(), approver="approver")
    platform.ctx.security.create_binding(
        wid, "admin", subject_type="user", subject_id="bob",
        role="station_manager")
    rows = [
        {"ref": "Vehicle/V1", "properties": {
            "station_id": "S1", "plate": "A12345"}},
        {"ref": "Vehicle/V2", "properties": {
            "station_id": "S2", "plate": "B99999"}},
    ]
    visible = platform.security.filter_objects(
        wid, "bob", rows, resource_type="Vehicle",
        attributes={"station_id": "S1"})
    assert [row["ref"] for row in visible] == ["Vehicle/V1"]
    assert visible[0]["properties"]["plate"] != "A12345"
    assert "***" in visible[0]["properties"]["plate"]


def test_scoped_repository_pins_tenant_and_filters_rows(platform):
    wid = seed_workspace(platform)
    _owners_who_can_publish(platform, wid, "approver")
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    _publish(platform, wid, _scoped_vehicle_document(), approver="approver")
    platform.ctx.security.create_binding(
        wid, "admin", subject_type="user", subject_id="bob",
        role="station_manager")
    principal = platform.ctx.security.principal(
        "bob", wid, attributes={"station_id": "S1"})
    tenant = platform.ctx.security.tenant_context(wid)
    from onto_platform.domain.security.model import Resource

    decision = platform.ctx.security.decision_for(
        principal, tenant, "object.read",
        Resource(type="Vehicle", workspace_id=wid), check_scope=False)
    scope = platform.ctx.security.authorized_scope(decision, principal, tenant)
    repo = ScopedRepository(platform.ctx.repo, scope)
    # Seed through the raw repository: the scoped repository must reject these
    # out-of-scope writes, so it is deliberately not used for fixture setup.
    platform.ctx.repo.raw.put("vehicle", wid, "V1",
                              {"station_id": "S1", "plate": "A12345"})
    platform.ctx.repo.raw.put("vehicle", wid, "V2",
                              {"station_id": "S2", "plate": "B99999"})
    listed = repo.list("vehicle", wid)
    assert [row.get("station_id") for row in listed] == ["S1"]
    assert repo.get("vehicle", wid, "V2") is None
    with pytest.raises(AccessDeniedError):
        repo.put("vehicle", wid, "V3", {"station_id": "S2"})
    with pytest.raises(AccessDeniedError):
        repo.delete("vehicle", wid, "V2")
    with pytest.raises(AccessDeniedError):
        repo.list("vehicle", seed_workspace(platform))


def test_role_binding_rejects_resource_scope(platform):
    wid = seed_workspace(platform)
    with pytest.raises(ValidationError):
        platform.ctx.security.create_binding(
            wid, "admin", subject_type="user", subject_id="bob",
            role="editor", scope_type="resource", scope_id="Vehicle/V1")


def test_role_binding_condition_uses_principal_attributes(platform):
    wid = seed_workspace(platform)
    platform.ctx.security.create_binding(
        wid, "admin", subject_type="user", subject_id="bob", role="viewer",
        conditions={"field": "tenant_id", "op": "eq",
                    "valueFrom": "principal.attributes.tenant_alias"})
    assert platform.ctx.security.principal(
        "bob", wid, attributes={"tenant_alias": wid}).roles == ("viewer",)
    assert platform.ctx.security.principal(
        "bob", wid, attributes={"tenant_alias": "other"}).roles == ()


# ---------------------------------------------------------------------------
# Agent delegation
# ---------------------------------------------------------------------------


def test_agent_never_escalates_and_fails_closed_without_caller(platform):
    wid = seed_workspace(platform)
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")
    caller = platform.ctx.security.principal("bob", wid)
    agent = platform.ctx.security.agent_principal("bob", wid)
    assert set(agent.roles) <= set(caller.roles)
    assert agent.on_behalf_of == "bob"
    # No caller context still resolves to a principal with no roles → deny.
    unknown = platform.ctx.security.agent_principal("nobody", wid)
    assert unknown.roles == ()
    tenant = platform.ctx.security.tenant_context(wid)
    from onto_platform.domain.security.model import Resource

    denied = platform.ctx.security.decision_for(
        unknown, tenant, "capability.invoke",
        Resource(type="capability", id="recharge", workspace_id=wid))
    assert denied.allow is False
    assert denied.reason == "role_not_allowed"


# ---------------------------------------------------------------------------
# Blueprint security block
# ---------------------------------------------------------------------------


def _blueprint_with_security() -> dict:
    return {
        "context": {"object": "Vehicle"},
        "security": {
            "schemaVersion": "1.0",
            "tenant": {"boundary": "workspace", "claim": "tenant_id"},
            "roles": [{"id": "station_manager", "label": "站长",
                       "inherits": ["viewer"]}],
            "scopes": {"station_self": {"match": {"all": [
                {"field": "tenant_id", "op": "eq",
                 "valueFrom": "principal.tenant_id"},
                {"field": "station_id", "op": "eq",
                 "valueFrom": "principal.attributes.station_id"},
            ]}}},
            "actionPolicies": [{
                "action": "pass_record.read", "resource": "Vehicle",
                "allowRoles": ["station_manager"],
                "scope": "station_self",
                "fields": {"plate": {"effect": "mask", "mask": "plate"}},
            }],
            "approval": {"requiredFor": ["policy.publish"],
                         "separationOfDuties": True},
        },
    }


def test_blueprint_security_normalizes_and_validates():
    from onto_platform.application.security.blueprint import (
        validate_blueprint_security,
    )

    report = validate_blueprint_security(
        _blueprint_with_security(), known_objects={"Vehicle"},
        known_actions={"pass_record.read"},
        known_fields={"Vehicle": {"plate", "station_id"}})
    assert report["present"] is True
    assert report["valid"] is True
    assert "station_manager" in report["summary"]["roles"]
    assert report["summary"]["fieldMasks"] == 1
    document = report["document"]
    rules = {(rule["action"], rule["resource"]): rule
             for rule in document["actionPolicies"]}
    rule = rules[("pass_record.read", "Vehicle")]
    assert rule["scope"] == "station_self"
    assert rule["fieldMasks"][0]["mask"] == "plate"


def test_blueprint_security_rejects_unknown_names():
    from onto_platform.application.security.blueprint import (
        validate_blueprint_security,
    )

    report = validate_blueprint_security(
        _blueprint_with_security(), known_objects={"Other"},
        known_actions={"pass_record.read"},
        known_fields={"Vehicle": {"plate"}})
    assert report["valid"] is False
    assert any("unknown object" in error for error in report["errors"])


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


def test_preview_is_side_effect_free_but_opt_in_recording_persists(platform):
    wid = seed_workspace(platform)
    preview = platform.security.authorize(
        wid, "admin", "workspace.read", subject="admin", record=False)
    assert preview["principal"]["id"] == "admin"
    # The preview itself is side-effect free; the guarding PEP check is
    # evidence.  Seeding also emits legacy role allows, so assert on the
    # previewed action explicitly.
    rows = platform.ctx.security.list_decisions(wid)
    assert [row["action"] for row in rows if row["action"] == "workspace.read"] == []
    assert any(row["action"] == "security.read" for row in rows)
    platform.security.authorize(wid, "admin", "workspace.read", record=True)
    rows = platform.ctx.security.list_decisions(wid)
    assert sum(1 for row in rows if row["action"] == "workspace.read") == 1


def test_security_http_routes(http_server):
    base, wid = http_server
    status, policies = http_get(
        base, f"/api/workspaces/{wid}/security/policies?user=admin")
    assert status == 200
    assert policies["active"]["id"] == "builtin"

    status, decision = http_post(
        base, f"/api/workspaces/{wid}/security/authorize",
        {"user": "admin", "action": "workspace.read"})
    assert status == 200
    assert decision["decision"]["allow"] is True

    status, preview = http_post(
        base, f"/api/workspaces/{wid}/security/authorize/preview",
        {"user": "admin", "action": "workspace.read",
         "resource": {"type": "workspace", "id": wid}})
    assert status == 200
    assert preview["decision"]["allow"] is True

    status, decisions = http_get(
        base, f"/api/workspaces/{wid}/security/decisions?user=admin")
    assert status == 200
    assert decisions
    decision_id = decisions[0]["decision_id"]
    status, one = http_get(
        base, f"/api/workspaces/{wid}/security/decisions/{decision_id}"
              f"?user=admin")
    assert status == 200
    assert one["decision_id"] == decision_id

    status, roles = http_get(
        base, f"/api/workspaces/{wid}/security/roles?user=admin")
    assert status == 200
    assert any(role["id"] == "owner" for role in roles["roles"])

    status, scopes = http_get(
        base, f"/api/workspaces/{wid}/security/scopes?user=admin")
    assert status == 200
    assert "workspace_scope" in scopes["scopes"]

    status, blueprint = http_post(
        base, f"/api/workspaces/{wid}/security/blueprint/validate",
        {"user": "admin", "blueprint": _blueprint_with_security()})
    assert status == 200
    assert blueprint["valid"] is True


def test_security_http_policy_lifecycle_and_binding(http_server, platform):
    base, wid = http_server
    _owners_who_can_publish(platform, wid, "approver")
    status, created = http_post(
        base, f"/api/workspaces/{wid}/security/policies",
        {"user": "admin", "document": _viewer_document()})
    assert status == 200
    policy_id = created["id"]

    status, updated = http_put(
        base, f"/api/workspaces/{wid}/security/policies/{policy_id}",
        {"user": "admin", "document": _viewer_document(), "note": "draft",
         "expected_version": created["version"]})
    assert status == 200
    assert updated["status"] == "validated"

    status, missing_version = http_put(
        base, f"/api/workspaces/{wid}/security/policies/{policy_id}",
        {"user": "admin", "document": _viewer_document()})
    assert status == 400
    assert "expected_version" in missing_version["error"]

    status, bad = http_post(
        base, f"/api/workspaces/{wid}/security/policies/{policy_id}/approve",
        {"user": "admin"})
    assert status == 400
    assert "separation" in bad["error"].lower()

    status, approved = http_post(
        base, f"/api/workspaces/{wid}/security/policies/{policy_id}/approve",
        {"user": "approver"})
    assert status == 200
    assert approved["status"] == "approved"

    status, published = http_post(
        base, f"/api/workspaces/{wid}/security/policies/{policy_id}/publish",
        {"user": "approver", "expected_version": approved["version"]})
    assert status == 200
    assert published["status"] == "published"

    status, binding = http_post(
        base, f"/api/workspaces/{wid}/security/bindings",
        {"user": "admin", "subject_type": "user", "subject_id": "bob",
         "role": "viewer"})
    assert status == 200
    binding_id = binding["id"]
    status, listed = http_get(
        base, f"/api/workspaces/{wid}/security/bindings?user=admin")
    assert status == 200
    assert any(item["id"] == binding_id for item in listed)
    status, _ = http_delete(
        base, f"/api/workspaces/{wid}/security/bindings/{binding_id}"
              f"?user=admin")
    assert status == 200


def test_state_find_applies_policy_scope_and_mask(http_server, platform):
    base, wid = http_server
    document = _viewer_document()
    document["scopes"]["free_only"] = {"filter": {"all": [
        {"field": "tenant_id", "op": "eq",
         "valueFrom": "context.tenant_id"},
        {"field": "status", "op": "eq", "value": "free"},
    ]}}
    for rule in document["actionPolicies"]:
        if rule["action"] == "object.read" and rule["resource"] == "*":
            rule.update({
                "resource": "TollStation",
                "allowRoles": ["viewer"],
                "scope": "free_only",
                "fieldMasks": [{"field": "name", "mask": "redact"}],
            })
    _owners_who_can_publish(platform, wid, "approver")
    _publish(platform, wid, document, approver="approver")
    platform.workspaces.add_member(wid, "bob", "viewer", actor="admin")

    status, result = http_get(
        base,
        f"/api/workspaces/{wid}/state/find?type=TollStation&limit=50&user=bob")
    assert status == 200
    assert result["total"] == 1
    assert result["objects"][0]["properties"]["status"] == "free"
    assert result["objects"][0]["properties"]["name"] == "***"
