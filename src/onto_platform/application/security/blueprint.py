"""Blueprint ``security`` block: normalize, validate, and explain.

The LLM proposes a Blueprint; it never publishes a policy.  This module turns
the *declarative* Blueprint block (design §6) into the canonical policy
document used by the PDP, validates names against the workspace's real
objects/actions/fields, and returns a report the console can show before a
human approves the change.

Blueprint form (see design §6.1)::

    security:
      schemaVersion: "1.0"
      tenant: {boundary: workspace, claim: tenant_id}
      roles: [{id: station_manager, inherits: [viewer], label: 站长}]
      scopes: {station_self: {match: {all: [...]}}}
      actionPolicies:
        - action: pass_record.read
          resource: VehiclePassRecord
          allowRoles: [station_manager]
          scope: station_self
          fields: {plate_number: {effect: mask, mask: plate}}
      approval: {requiredFor: [...], separationOfDuties: true}
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from ...domain.security.model import policy_hash
from ...errors import ValidationError
from .masking import MASKERS
from .policy import (
    default_policy_document,
    policy_validation_report,
    role_graph_of,
)

BLUEPRINT_SECURITY_KEYS = frozenset({
    "schemaVersion", "tenant", "roles", "scopes", "actionPolicies",
    "fieldPolicies", "approval", "metadata",
})


def extract_security_block(blueprint: Mapping[str, Any] | None) -> dict | None:
    if not isinstance(blueprint, Mapping):
        return None
    block = blueprint.get("security")
    if block is None:
        return None
    if not isinstance(block, Mapping):
        raise ValidationError("blueprint.security must be an object")
    return dict(block)


def normalize_blueprint_security(block: Mapping[str, Any] | None, *,
                                 legacy: Any | None = None) -> dict:
    """Convert Blueprint shorthand into the canonical policy document.

    Platform built-ins (viewer/editor/owner + workspace scope + legacy action
    matrix) are merged in, so an app-level block only has to declare its own
    roles, scopes and app actions.  A block can still override a default rule
    by naming the same action/resource pair.
    """
    document = default_policy_document(legacy)
    if block is None:
        return document
    if not isinstance(block, Mapping):
        raise ValidationError("blueprint.security must be an object")
    unknown = set(block) - BLUEPRINT_SECURITY_KEYS
    if unknown:
        raise ValidationError(f"unknown blueprint.security keys: "
                              f"{sorted(unknown)}")

    roles_by_id = {role["id"]: dict(role) for role in document["roles"]}
    for raw in block.get("roles") or ():
        if not isinstance(raw, Mapping):
            raise ValidationError("blueprint.security.roles must be objects")
        role_id = str(raw.get("id") or "")
        if not role_id:
            raise ValidationError("blueprint security role needs an id")
        entry = roles_by_id.get(role_id, {"id": role_id})
        if raw.get("label") and not raw.get("displayName"):
            entry["displayName"] = str(raw["label"])
        if raw.get("displayName"):
            entry["displayName"] = str(raw["displayName"])
        if raw.get("inherits") is not None:
            entry["inherits"] = [str(parent) for parent in raw["inherits"]]
        if raw.get("description"):
            entry["description"] = str(raw["description"])
        roles_by_id[role_id] = entry
    document["roles"] = list(roles_by_id.values())

    tenant = dict(document.get("tenant") or {})
    incoming_tenant = block.get("tenant") or {}
    if isinstance(incoming_tenant, Mapping):
        tenant.update({key: incoming_tenant[key] for key in incoming_tenant
                       if key in ("boundary", "claim")})
    tenant["enforce"] = True
    document["tenant"] = tenant

    scopes = dict(document.get("scopes") or {})
    for name, raw in (block.get("scopes") or {}).items():
        if not isinstance(raw, Mapping):
            raise ValidationError(f"scope {name!r} must be an object")
        if "filter" in raw:
            filt = raw["filter"]
        elif "match" in raw:
            filt = raw["match"]
        else:
            raise ValidationError(
                f"scope {name!r} must contain 'match' or 'filter'")
        scopes[str(name)] = {"filter": deepcopy(filt)}
    document["scopes"] = scopes

    existing_rules = {
        (rule.get("action"), rule.get("resource")): rule
        for rule in document.get("actionPolicies") or ()
    }
    for raw in block.get("actionPolicies") or ():
        if not isinstance(raw, Mapping):
            raise ValidationError(
                "blueprint.security.actionPolicies must be objects")
        rule = _normalize_action_rule(raw)
        existing_rules[(rule["action"], rule["resource"])] = rule
    document["actionPolicies"] = list(existing_rules.values())

    field_policies = list(document.get("fieldPolicies") or [])
    field_policies.extend(_normalize_field_policies(block.get("fieldPolicies")))
    document["fieldPolicies"] = field_policies

    approval = dict(document.get("approval") or {})
    incoming = block.get("approval") or {}
    if isinstance(incoming, Mapping):
        if isinstance(incoming.get("requiredFor"), list):
            approval["requiredFor"] = list(dict.fromkeys(
                list(approval.get("requiredFor") or [])
                + [str(item) for item in incoming["requiredFor"]]))
        if "separationOfDuties" in incoming:
            approval["separationOfDuties"] = bool(
                incoming["separationOfDuties"])
        if "approvers" in incoming:
            approval["approvers"] = list(incoming["approvers"])
    document["approval"] = approval

    if block.get("metadata"):
        document["metadata"] = {
            **(document.get("metadata") or {}),
            **(dict(block["metadata"]) if isinstance(block["metadata"], Mapping)
               else {}),
            "source": "blueprint",
        }
    return document


def _normalize_action_rule(raw: Mapping[str, Any]) -> dict:
    action = str(raw.get("action") or "")
    resource = str(raw.get("resource") or "")
    if not action or not resource:
        raise ValidationError("blueprint action policy needs action/resource")
    rule = {
        "action": action,
        "resource": resource,
        "allowRoles": [str(role) for role in raw.get("allowRoles") or ()],
    }
    for key in ("scope", "relation", "description"):
        if raw.get(key):
            rule[key] = str(raw[key])
    if raw.get("fields"):
        rule["fieldMasks"] = _fields_to_masks(raw["fields"])
    if raw.get("fieldMasks"):
        rule["fieldMasks"] = list(rule.get("fieldMasks") or []) \
            + _fields_to_masks(raw["fieldMasks"])
    if raw.get("obligations"):
        rule["obligations"] = [dict(item) for item in raw["obligations"]
                               if isinstance(item, Mapping)]
    if raw.get("conditions"):
        rule["conditions"] = deepcopy(raw["conditions"])
    return rule


def _fields_to_masks(fields: Any) -> list[dict]:
    if isinstance(fields, list):
        return [dict(item) for item in fields if isinstance(item, Mapping)]
    if not isinstance(fields, Mapping):
        raise ValidationError("blueprint fields must be an object or list")
    masks: list[dict] = []
    for field, raw in fields.items():
        if isinstance(raw, str):
            masks.append({"field": str(field), "mask": raw})
            continue
        if not isinstance(raw, Mapping):
            raise ValidationError(f"field mask {field!r} must be an object")
        effect = str(raw.get("effect") or "mask")
        mask = str(raw.get("mask") or "redact")
        if effect == "strip":
            mask = "strip"
        elif effect not in ("mask", "none"):
            raise ValidationError(f"unknown field effect {effect!r}")
        masks.append({"field": str(field), "mask": mask})
    return masks


def _normalize_field_policies(policies: Any) -> list[dict]:
    if not policies:
        return []
    if isinstance(policies, Mapping):
        return _fields_to_masks(policies)
    if not isinstance(policies, list):
        raise ValidationError("fieldPolicies must be a list")
    return [dict(item) for item in policies if isinstance(item, Mapping)]


def validate_blueprint_security(
        blueprint: Mapping[str, Any] | None, *,
        known_objects: Iterable[str] | None = None,
        known_actions: Iterable[str] | None = None,
        known_fields: Mapping[str, Iterable[str]] | None = None,
) -> dict:
    """Validate a Blueprint security block without mutating the blueprint.

    Returns ``{present, valid, errors, warnings, hash, document, summary}``.
    A blueprint without a security block is valid and simply inherits the
    workspace's active policy.
    """
    block = extract_security_block(blueprint)
    if block is None:
        return {"present": False, "valid": True, "errors": [], "warnings": [],
                "hash": "", "document": None, "summary": {}}
    try:
        document = normalize_blueprint_security(block)
    except ValidationError as exc:
        return {"present": True, "valid": False, "errors": [str(exc)],
                "warnings": [], "hash": "", "document": None,
                "summary": {}}
    report = policy_validation_report(document)
    errors = list(report["errors"])
    warnings = list(report["warnings"])

    objects = set(known_objects or ())
    actions = set(known_actions or ())
    fields = {str(key): set(value) for key, value in (known_fields or {}).items()}
    defaults = default_policy_document()
    builtin_pairs = {
        (str(rule.get("action") or ""), str(rule.get("resource") or ""))
        for rule in defaults.get("actionPolicies") or ()
    }
    for rule in document.get("actionPolicies") or ():
        resource = str(rule.get("resource") or "")
        action = str(rule.get("action") or "")
        if (action, resource) in builtin_pairs:
            # Platform built-ins are merged into the document; they are not
            # names proposed by this blueprint and must not fail app-level
            # object/action checks.
            continue
        if objects and resource not in ("*", "") and resource not in objects:
            errors.append(
                f"security action {action!r} references unknown object "
                f"{resource!r}")
        if actions and action not in actions:
            errors.append(
                f"security references unknown action {action!r}")
        for spec in rule.get("fieldMasks") or ():
            field = str(spec.get("field") or "")
            allowed = fields.get(resource)
            if allowed and field not in allowed:
                errors.append(
                    f"security field {field!r} is not declared on "
                    f"{resource!r}")
            mask = str(spec.get("mask") or "")
            if mask not in MASKERS:
                errors.append(f"unknown field mask {mask!r}")
    for role in document.get("roles") or ():
        inherit = role.get("inherits") or []
        for parent in inherit:
            if parent not in {item.get("id") for item in document["roles"]}:
                errors.append(
                    f"role {role.get('id')!r} inherits unknown {parent!r}")
    # A block that closes the tenant condition would be a cross-tenant leak.
    for name, scope in (document.get("scopes") or {}).items():
        try:
            from ...domain.security.filters import normalize_filter
            normalize_filter(scope.get("filter") or {}, require_tenant=True)
        except ValidationError as exc:
            errors.append(f"scope {name!r}: {exc}")
    return {
        "present": True,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "hash": policy_hash(document),
        "document": document,
        "summary": report.get("summary") or {},
        "roleGraph": role_graph_of(document),
    }


__all__ = [
    "BLUEPRINT_SECURITY_KEYS",
    "extract_security_block",
    "normalize_blueprint_security",
    "validate_blueprint_security",
]
