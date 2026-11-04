"""Policy decision point: deterministic, side-effect free evaluation.

Order of checks is part of the contract:

1. principal is active;
2. principal/context tenant agrees with the resource tenant;
3. exactly one matching action policy exists (no match = DENY);
4. an allowed role (with inheritance) is present;
5. ReBAC relation, if the rule requires one;
6. ABAC/scope filter matches the resource;
7. every obligation is enforceable.

The evaluator never calls a model and never writes; the service layer stamps
ids/timestamps and decides whether to persist the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from ...domain.security.filters import evaluate_filter, normalize_filter
from ...domain.security.model import (
    Decision,
    Obligation,
    Principal,
    Resource,
    TenantContext,
)
from .policy import expand_roles, role_graph_of

SUPPORTED_OBLIGATIONS = frozenset({"row_filter", "field_mask", "audit"})


@dataclass(slots=True)
class PolicyRule:
    action: str
    resource: str
    allow_roles: tuple[str, ...]
    deny_roles: tuple[str, ...] = ()
    scope: str | None = None
    relation: str | None = None
    field_masks: tuple[dict, ...] = ()
    obligations: tuple[dict, ...] = ()
    conditions: dict | None = None
    description: str = ""

    @property
    def specificity(self) -> int:
        return 2 if self.resource not in ("*", "") else 1


class PolicyEvaluator:
    """Evaluate one policy document against one request."""

    def __init__(self, record: Any | None = None, *,
                 document: Mapping[str, Any] | None = None,
                 policy_id: str = "", policy_version: int = 0) -> None:
        if document is None:
            if record is None:
                raise ValueError("policy document or record is required")
            document = getattr(record, "document", None)
            if document is None and isinstance(record, Mapping):
                document = record
        if not isinstance(document, Mapping):
            raise ValueError("policy document must be an object")
        self.document = document
        self.policy_id = policy_id or getattr(record, "id", "") or "builtin"
        self.policy_version = int(
            policy_version or getattr(record, "version", 0) or 0)
        self.graph = role_graph_of(document)
        self.roles = {role.get("id") for role in document.get("roles") or ()
                      if isinstance(role, Mapping)}
        self.scopes: dict[str, dict] = {}
        for name, scope in (document.get("scopes") or {}).items():
            if isinstance(scope, Mapping) and "filter" in scope:
                self.scopes[name] = normalize_filter(scope["filter"],
                                                     require_tenant=False)
        self.rules: list[PolicyRule] = []
        for raw in document.get("actionPolicies") or ():
            if not isinstance(raw, Mapping):
                continue
            action = raw.get("action")
            resource = raw.get("resource")
            if not isinstance(action, str) or not isinstance(resource, str):
                continue
            self.rules.append(PolicyRule(
                action=action,
                resource=resource,
                allow_roles=tuple(str(role)
                                  for role in raw.get("allowRoles") or ()),
                deny_roles=tuple(str(role)
                                 for role in raw.get("denyRoles") or ()),
                scope=(str(raw["scope"]) if raw.get("scope") else None),
                relation=(str(raw["relation"]) if raw.get("relation") else None),
                field_masks=tuple(spec for spec in raw.get("fieldMasks") or ()
                                  if isinstance(spec, Mapping)),
                obligations=tuple(ob for ob in raw.get("obligations") or ()
                                  if isinstance(ob, Mapping)),
                conditions=(normalize_filter(raw["conditions"])
                            if raw.get("conditions") else None),
                description=str(raw.get("description", "")),
            ))
        self.field_policies = tuple(
            spec for spec in document.get("fieldPolicies") or ()
            if isinstance(spec, Mapping))
        self.tenant = document.get("tenant") or {}

    # ---- policy lookup ----

    def matching_rules(self, action: str,
                       resource_type: str) -> list[PolicyRule]:
        return [rule for rule in self.rules
                if rule.action == action
                and (rule.resource == "*" or rule.resource == resource_type)]

    def rule_for(self, action: str, resource_type: str) -> PolicyRule | None:
        candidates = self.matching_rules(action, resource_type)
        if not candidates:
            return None
        # Exact resource beats wildcard; the first exact rule wins deterministically.
        candidates.sort(key=lambda rule: (-rule.specificity, rule.resource,
                                          rule.scope or "", rule.relation or ""))
        exact = [rule for rule in candidates if rule.resource != "*"]
        if exact:
            return exact[0]
        return candidates[0]

    def roles_allow(self, principal: Principal,
                    rule: PolicyRule) -> bool:
        granted = expand_roles(principal.roles, self.graph)
        return any(role in granted for role in rule.allow_roles)

    # ---- decision ----

    def evaluate(self, principal: Principal, tenant: TenantContext,
                 action: str, resource: Resource, *,
                 relation_reader: Callable[[str, str, str, str], bool] | None = None,
                 check_scope: bool = True,
                 ) -> Decision:
        base = dict(
            action=action,
            principal_id=principal.id,
            tenant_id=tenant.tenant_id,
            workspace_id=tenant.workspace_id,
            resource_type=resource.type,
            resource_id=resource.id,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
        )
        if not principal.active:
            return Decision(allow=False, reason="principal_inactive", **base)
        if principal.tenant_id and principal.tenant_id != tenant.tenant_id:
            return Decision(allow=False, reason="principal_tenant_mismatch",
                            **base)
        if resource.tenant_id and resource.tenant_id != tenant.tenant_id:
            return Decision(allow=False, reason="resource_tenant_mismatch",
                            **base)
        if resource.workspace_id and resource.workspace_id != tenant.workspace_id:
            return Decision(allow=False, reason="resource_workspace_mismatch",
                            **base)

        rule = self.rule_for(action, resource.type)
        if rule is None:
            return Decision(allow=False, reason="no_matching_action_policy",
                            **base)
        # Deny overrides allow: a matching denyRoles on any candidate rule wins.
        granted = expand_roles(principal.roles, self.graph)
        matching = self.matching_rules(action, resource.type)
        if any(role in granted
               for candidate in matching
               for role in candidate.deny_roles):
            return Decision(allow=False, reason="explicit_deny", **base)
        if not self.roles_allow(principal, rule):
            return Decision(allow=False, reason="role_not_allowed", **base)
        if rule.relation:
            if relation_reader is None or not resource.id:
                return Decision(allow=False, reason="relation_not_configured",
                                **base)
            if not relation_reader(resource.type, resource.id, rule.relation,
                                   principal.id):
                return Decision(allow=False, reason="relation_denied", **base)
        # The tenant context is authoritative for scope evaluation; a caller
        # cannot smuggle a different tenant_id through resource attributes.
        record = {**resource.record(),
                  "tenant_id": tenant.tenant_id,
                  "workspace_id": tenant.workspace_id}
        if rule.conditions is not None and not evaluate_filter(
                rule.conditions, principal, tenant.to_dict(), record):
            return Decision(allow=False, reason="condition_denied", **base)

        scope_filter = None
        obligations: list[Obligation] = []
        if rule.scope:
            scope_filter = self.scopes.get(rule.scope)
            if scope_filter is None:
                return Decision(allow=False, reason="scope_missing", **base)
            if check_scope and not evaluate_filter(
                    scope_filter, principal, tenant.to_dict(), record):
                return Decision(allow=False, reason="scope_denied", **base)
            obligations.append(Obligation(kind="row_filter",
                                          value={"filter": scope_filter}))

        for spec in rule.field_masks:
            obligations.append(Obligation(
                kind="field_mask", value={"fields": [dict(spec)]}))
        for raw in rule.obligations:
            kind = str(raw.get("kind") or "")
            value = dict(raw.get("value") or {})
            if kind not in SUPPORTED_OBLIGATIONS:
                return Decision(
                    allow=False, reason=f"unresolved_obligation:{kind}",
                    **base)
            obligations.append(Obligation(kind=kind, value=value,
                                          enforced=bool(raw.get("enforced", True))))
        # Global masks are mandatory; they cannot be dropped by a caller.
        if self.field_policies:
            obligations.append(Obligation(
                kind="field_mask",
                value={"fields": [dict(spec) for spec in self.field_policies]}))
        if any(not obligation.enforced for obligation in obligations):
            return Decision(allow=False, reason="unresolved_obligation", **base)

        if any(ob.kind == "row_filter" for ob in obligations):
            # make the filter explicit on the decision for the PEP and console
            pass
        return Decision(
            allow=True,
            reason="role_and_scope_match",
            obligations=tuple(_dedupe_obligations(obligations)),
            scope=scope_filter,
            **base,
        )


def _dedupe_obligations(obligations: Iterable[Obligation]) -> list[Obligation]:
    seen: set[tuple] = set()
    out: list[Obligation] = []
    for obligation in obligations:
        key = (obligation.kind, repr(sorted(obligation.value.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(obligation)
    return out


__all__ = ["PolicyEvaluator", "PolicyRule", "SUPPORTED_OBLIGATIONS"]
