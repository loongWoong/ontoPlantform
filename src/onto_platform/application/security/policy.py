"""Policy document schema, built-in policy, validation and diffing.

The built-in policy is generated from the legacy action matrix so every
existing use case keeps working while the unified PDP becomes the only
decision point.  A published custom policy replaces it for new decisions; the
built-in remains the safe fallback for a workspace that has never configured
one (and for a fresh test fixture).
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from ...domain.governance.model import AccessPolicy
from ...domain.security.filters import normalize_filter
from ...domain.security.model import (
    POLICY_SCHEMA_VERSION,
    ROLE_INHERITS,
    ROLES,
    policy_hash,
)
from ...errors import ValidationError
from .masking import MASKERS, validate_mask_spec

_ROLE_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.:-]*$")
_RESOURCE = re.compile(r"^[A-Za-z_*][A-Za-z0-9_.:-]*$")
_SCOPE_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")
_RELATION = re.compile(r"^[a-z][a-z0-9_.-]*$")

POLICY_KEYS = frozenset({
    "schemaVersion", "tenant", "roles", "scopes", "actionPolicies",
    "fieldPolicies", "approval", "metadata",
})
ACTION_RULE_KEYS = frozenset({
    "action", "resource", "allowRoles", "denyRoles", "scope", "relation",
    "fieldMasks", "obligations", "conditions", "description",
})
ROLE_KEYS = frozenset({"id", "displayName", "inherits", "description"})
APPROVAL_DEFAULTS = {
    "requiredFor": ["policy.publish"],
    "separationOfDuties": True,
    "minApprovers": 1,
}


def builtin_scope_document(workspace_key: str = "workspace_scope") -> dict:
    return {
        "filter": {
            "all": [
                {"field": "tenant_id", "op": "eq",
                 "valueFrom": "context.tenant_id"},
                {"field": "workspace_id", "op": "eq",
                 "valueFrom": "context.workspace_id"},
            ]
        }
    }


def default_policy_document(legacy: AccessPolicy | None = None) -> dict:
    """Deterministic built-in policy mirroring ``AccessPolicy.ACTION_POLICY``."""
    legacy = legacy or AccessPolicy()
    roles = [
        {"id": "viewer", "displayName": "Viewer",
         "description": "Read workspace content"},
        {"id": "editor", "displayName": "Editor", "inherits": ["viewer"],
         "description": "Edit definitions and run projections"},
        {"id": "owner", "displayName": "Owner", "inherits": ["editor"],
         "description": "Manage members, releases and security"},
    ]
    actions = []
    for action, (required_role, target) in sorted(legacy.ACTION_POLICY.items()):
        actions.append({
            "action": action,
            "resource": target,
            "allowRoles": [required_role],
            "scope": "workspace_scope",
            "description": f"legacy require {required_role} on {target}",
        })
    return {
        "schemaVersion": POLICY_SCHEMA_VERSION,
        "tenant": {"boundary": "workspace", "enforce": True},
        "roles": roles,
        "scopes": {"workspace_scope": builtin_scope_document()},
        "actionPolicies": actions,
        "fieldPolicies": [],
        "approval": deepcopy(APPROVAL_DEFAULTS),
        "metadata": {
            "id": "builtin",
            "name": "Built-in workspace policy",
            "source": "legacy-action-matrix",
        },
    }


def policy_validation_report(document: Mapping[str, Any]) -> dict:
    """Return ``{valid, errors, warnings, hash, summary}`` without raising."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(document, Mapping):
        return {"valid": False, "errors": ["policy must be an object"],
                "warnings": [], "hash": "", "summary": {}}

    unknown = set(document) - POLICY_KEYS
    if unknown:
        errors.append(f"unknown policy keys: {sorted(unknown)}")
    if document.get("schemaVersion") != POLICY_SCHEMA_VERSION:
        errors.append(
            f"schemaVersion must be {POLICY_SCHEMA_VERSION!r}")

    tenant = document.get("tenant")
    if not isinstance(tenant, Mapping):
        errors.append("tenant must be an object")
        tenant = {}
    boundary = tenant.get("boundary", "workspace")
    if boundary not in ("workspace", "tenant"):
        errors.append("tenant.boundary must be 'workspace' or 'tenant'")
    if tenant.get("enforce") is not True:
        errors.append("tenant.enforce must be true")

    role_graph: dict[str, tuple[str, ...]] = {}
    roles = document.get("roles")
    if not isinstance(roles, list) or not roles:
        errors.append("roles must be a non-empty list")
        roles = []
    for role in roles:
        if not isinstance(role, Mapping):
            errors.append("each role must be an object")
            continue
        unknown = set(role) - ROLE_KEYS
        if unknown:
            errors.append(f"unknown role keys: {sorted(unknown)}")
        rid = role.get("id")
        if not isinstance(rid, str) or not _ROLE_ID.match(rid):
            errors.append(f"invalid role id {rid!r}")
            continue
        if rid in role_graph:
            errors.append(f"duplicate role {rid!r}")
            continue
        inherits = role.get("inherits") or []
        if not isinstance(inherits, list) or any(
                not isinstance(item, str) or not _ROLE_ID.match(item)
                for item in inherits):
            errors.append(f"role {rid!r} inherits must be role ids")
            inherits = []
        role_graph[rid] = tuple(inherits)
    missing_builtins = [role for role in ROLES if role not in role_graph]
    if missing_builtins:
        errors.append(
            f"built-in roles must remain defined: {missing_builtins}")
    if not errors:
        for rid, parents in role_graph.items():
            for parent in parents:
                if parent not in role_graph:
                    errors.append(
                        f"role {rid!r} inherits unknown role {parent!r}")
        _check_role_cycles(role_graph, errors)

    scopes_doc = document.get("scopes")
    if not isinstance(scopes_doc, Mapping) or not scopes_doc:
        errors.append("scopes must be a non-empty object")
        scopes_doc = {}
    for name, scope in scopes_doc.items():
        if not isinstance(name, str) or not _SCOPE_ID.match(name):
            errors.append(f"invalid scope name {name!r}")
            continue
        if not isinstance(scope, Mapping) or "filter" not in scope:
            errors.append(f"scope {name!r} must contain a filter")
            continue
        try:
            normalize_filter(scope["filter"], require_tenant=True)
        except ValidationError as exc:
            errors.append(f"scope {name!r}: {exc}")

    actions_doc = document.get("actionPolicies")
    if not isinstance(actions_doc, list) or not actions_doc:
        errors.append("actionPolicies must be a non-empty list")
        actions_doc = []
    seen_rules: set[tuple[str, str]] = set()
    action_names: set[str] = set()
    for rule in actions_doc:
        if not isinstance(rule, Mapping):
            errors.append("each action policy must be an object")
            continue
        unknown = set(rule) - ACTION_RULE_KEYS
        if unknown:
            errors.append(f"unknown action policy keys: {sorted(unknown)}")
        action = rule.get("action")
        resource = rule.get("resource")
        if not isinstance(action, str) or not _ACTION.match(action):
            errors.append(f"invalid action {action!r}")
            continue
        action_names.add(action)
        if not isinstance(resource, str) or not _RESOURCE.match(resource):
            errors.append(f"action {action!r}: invalid resource {resource!r}")
            continue
        key = (action, resource)
        if key in seen_rules:
            errors.append(f"duplicate action policy {action!r}/{resource!r}")
        seen_rules.add(key)
        allow_roles = rule.get("allowRoles")
        if not isinstance(allow_roles, list) or not allow_roles:
            errors.append(f"action {action!r}: allowRoles must be non-empty")
        else:
            for role in allow_roles:
                if role not in role_graph:
                    errors.append(
                        f"action {action!r}: unknown role {role!r}")
        deny_roles = rule.get("denyRoles") or []
        if not isinstance(deny_roles, list):
            errors.append(f"action {action!r}: denyRoles must be a list")
            deny_roles = []
        for role in deny_roles:
            if role not in role_graph:
                errors.append(
                    f"action {action!r}: unknown deny role {role!r}")
        if isinstance(allow_roles, list):
            overlap = sorted(set(allow_roles) & set(deny_roles))
            if overlap:
                warnings.append(
                    f"action {action!r}: roles both allowed and denied "
                    f"(deny wins): {overlap}")
        scope = rule.get("scope")
        if scope is not None and scope not in scopes_doc:
            errors.append(f"action {action!r}: unknown scope {scope!r}")
        relation = rule.get("relation")
        if relation is not None and (
                not isinstance(relation, str) or not _RELATION.match(relation)):
            errors.append(f"action {action!r}: invalid relation {relation!r}")
        field_masks = rule.get("fieldMasks") or []
        if not isinstance(field_masks, list):
            errors.append(f"action {action!r}: fieldMasks must be a list")
            field_masks = []
        for spec in field_masks:
            try:
                validate_mask_spec(spec)
            except ValidationError as exc:
                errors.append(f"action {action!r}: {exc}")
        conditions = rule.get("conditions")
        if conditions is not None:
            try:
                normalize_filter(conditions)
            except ValidationError as exc:
                errors.append(f"action {action!r} conditions: {exc}")
        obligations = rule.get("obligations") or []
        if not isinstance(obligations, list):
            errors.append(f"action {action!r}: obligations must be a list")
        else:
            for obligation in obligations:
                if not isinstance(obligation, Mapping) or "kind" not in obligation:
                    errors.append(
                        f"action {action!r}: obligation needs a kind")
                elif obligation["kind"] == "field_mask":
                    for spec in obligation.get("fields") or []:
                        try:
                            validate_mask_spec(spec)
                        except ValidationError as exc:
                            errors.append(f"action {action!r}: {exc}")

    field_policies = document.get("fieldPolicies", [])
    if not isinstance(field_policies, list):
        errors.append("fieldPolicies must be a list")
        field_policies = []
    for spec in field_policies:
        try:
            validate_mask_spec(spec)
        except ValidationError as exc:
            errors.append(f"field policy: {exc}")

    approval = document.get("approval")
    if not isinstance(approval, Mapping):
        errors.append("approval must be an object")
        approval = {}
    required_for = approval.get("requiredFor", [])
    if not isinstance(required_for, list) or any(
            not isinstance(item, str) for item in required_for):
        errors.append("approval.requiredFor must be a list of actions")
    elif "policy.publish" not in required_for:
        warnings.append("approval.requiredFor should include policy.publish")
    if approval.get("separationOfDuties") is not True:
        warnings.append("separationOfDuties is disabled in this policy")

    if not action_names:
        errors.append("actionPolicies must define at least one action")
    if not errors and "workspace.read" not in action_names:
        warnings.append("policy does not define workspace.read")

    mask_count = (len(field_policies)
                  if isinstance(field_policies, list) else 0)
    if isinstance(actions_doc, list):
        mask_count += sum(len(rule.get("fieldMasks") or [])
                          for rule in actions_doc
                          if isinstance(rule, Mapping))
    summary = {
        "roles": sorted(role_graph),
        "scopes": sorted(scopes_doc) if isinstance(scopes_doc, Mapping) else [],
        "actions": len(actions_doc) if isinstance(actions_doc, list) else 0,
        "fieldMasks": mask_count,
        "boundary": boundary,
    }
    try:
        digest = policy_hash(document)
    except (TypeError, ValueError):
        digest = ""
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "hash": digest,
        "summary": summary,
    }


def validate_policy_document(document: Mapping[str, Any], *,
                             raise_on_error: bool = True) -> dict:
    report = policy_validation_report(document)
    if raise_on_error and not report["valid"]:
        raise ValidationError(
            "invalid security policy: " + "; ".join(report["errors"]))
    return report


def _check_role_cycles(graph: Mapping[str, tuple[str, ...]],
                       errors: list[str]) -> None:
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        state[node] = 1
        for parent in graph.get(node, ()):  # parents are "higher" roles
            if state.get(parent) == 1:
                errors.append(f"role inheritance cycle at {parent!r}")
                return
            if state.get(parent, 0) == 0:
                visit(parent)
        state[node] = 2

    for role in graph:
        if state.get(role, 0) == 0:
            visit(role)


def expand_roles(direct: list[str] | tuple[str, ...],
                 graph: Mapping[str, tuple[str, ...]]) -> set[str]:
    """Transitive role closure; unknown roles are ignored (deny-by-default)."""
    out: set[str] = set()
    stack = [role for role in direct if role in graph]
    while stack:
        role = stack.pop()
        if role in out:
            continue
        out.add(role)
        stack.extend(parent for parent in graph.get(role, ())
                     if parent not in out)
    return out


def role_graph_of(document: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    graph: dict[str, tuple[str, ...]] = {}
    for role in document.get("roles") or ():
        if isinstance(role, Mapping) and isinstance(role.get("id"), str):
            graph[role["id"]] = tuple(role.get("inherits") or ())
    return graph


def policy_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict:
    """Structural diff for the console's review screen."""
    left_actions = {(rule.get("action"), rule.get("resource"))
                    for rule in left.get("actionPolicies") or ()
                    if isinstance(rule, Mapping)}
    right_actions = {(rule.get("action"), rule.get("resource"))
                     for rule in right.get("actionPolicies") or ()
                     if isinstance(rule, Mapping)}
    left_roles = {role.get("id") for role in left.get("roles") or ()
                  if isinstance(role, Mapping)}
    right_roles = {role.get("id") for role in right.get("roles") or ()
                   if isinstance(role, Mapping)}
    left_scopes = set(left.get("scopes") or {})
    right_scopes = set(right.get("scopes") or {})
    return {
        "actionsAdded": sorted([a for a in right_actions - left_actions if a]),
        "actionsRemoved": sorted([a for a in left_actions - right_actions if a]),
        "rolesAdded": sorted(r for r in right_roles - left_roles if r),
        "rolesRemoved": sorted(r for r in left_roles - right_roles if r),
        "scopesAdded": sorted(right_scopes - left_scopes),
        "scopesRemoved": sorted(left_scopes - right_scopes),
    }


__all__ = [
    "ACTION_RULE_KEYS",
    "APPROVAL_DEFAULTS",
    "MASKERS",
    "POLICY_KEYS",
    "ROLE_INHERITS",
    "builtin_scope_document",
    "default_policy_document",
    "expand_roles",
    "policy_diff",
    "policy_hash",
    "policy_validation_report",
    "role_graph_of",
    "validate_policy_document",
]
