"""Authorization domain model (design §5/§6).

The vocabulary is deliberately small and storage-agnostic:

    Principal      who is acting (user, service, agent, platform auditor)
    Tenant         the security boundary (workspace in V0.1)
    RoleBinding    subject -> role at a scope
    Policy         versioned, reviewable authorization document
    Decision       the only thing a PEP may act on

Nothing here calls a repository, an LLM, or the network.  That is what makes
the final ALLOW/DENY deterministic and testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Mapping

from ...errors import ValidationError

#: Built-in roles are a compatibility contract in V0.1.  Policies may add
#: roles, but these three keep their rank semantics for existing use cases.
ROLES = ("viewer", "editor", "owner")
ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}
ROLE_INHERITS = {
    "viewer": (),
    "editor": ("viewer",),
    "owner": ("editor",),
}

POLICY_STATUSES = frozenset({
    "draft", "validated", "approved", "published", "deprecated",
})
POLICY_SCHEMA_VERSION = "1.0"

PRINCIPAL_KINDS = frozenset({"user", "service", "agent", "platform_admin"})


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def policy_hash(document: Mapping[str, Any]) -> str:
    """Content hash of the *whole* policy document (not just its action list)."""
    return sha256(_canonical(document).encode("utf-8")).hexdigest()


def policy_risk_tier(document: Mapping[str, Any]) -> str:
    """Deterministic risk tier for an approval workflow.

    High: wildcard actions, owner-level security grants, or a tenant-wide
    scope that is not narrowed to a workspace.  Medium: many actions, field
    masks, or a role graph change.  Low: everything else.  An explicit
    ``approval.riskTier``/``metadata.riskTier`` always wins; the tier only
    changes the required approver count when ``approval.useRiskTiers`` is on,
    so existing one-approver policies keep their documented behaviour.
    """
    if not isinstance(document, Mapping):
        return "high"
    approval = document.get("approval") or {}
    explicit = str(approval.get("riskTier")
                   or (document.get("metadata") or {}).get("riskTier")
                   or "").strip().lower()
    if explicit in ("low", "medium", "high"):
        return explicit
    actions = document.get("actionPolicies") or []
    high = False
    medium = False
    if len(actions) > 8:
        medium = True
    for rule in actions:
        if not isinstance(rule, Mapping):
            high = True
            continue
        action = str(rule.get("action") or "")
        roles = [str(role) for role in (rule.get("allowRoles") or [])]
        if action in ("*", "security.publish", "security.manage") \
                or "owner" in roles:
            high = True
        if rule.get("fieldMasks"):
            medium = True
    tenant = document.get("tenant") or {}
    boundary = str(tenant.get("boundary") or "workspace")
    if boundary == "tenant":
        high = True
    if document.get("fieldPolicies"):
        medium = True
    if document.get("roles") and len(document["roles"]) > 3:
        medium = True
    if high:
        return "high"
    if medium:
        return "medium"
    return "low"


@dataclass(slots=True)
class Principal:
    """An authenticated actor.

    ``roles`` are direct grants; role inheritance is expanded by the PDP using
    the active policy's role graph, never by the caller.
    """

    id: str
    kind: str = "user"
    tenant_id: str = ""
    workspace_id: str = ""
    roles: tuple[str, ...] = ()
    attributes: dict = field(default_factory=dict)
    display_name: str = ""
    authn_method: str = "api"
    active: bool = True
    #: set when a service/agent acts on behalf of a human; the human remains
    #: the accountable subject for audit and for delegated checks
    on_behalf_of: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValidationError("principal id must not be empty")
        if self.kind not in PRINCIPAL_KINDS:
            raise ValidationError(f"unknown principal kind {self.kind!r}")
        self.roles = tuple(dict.fromkeys(str(role) for role in self.roles))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "roles": list(self.roles),
            "attributes": dict(self.attributes),
            "display_name": self.display_name,
            "authn_method": self.authn_method,
            "active": self.active,
            "on_behalf_of": self.on_behalf_of,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Principal":
        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind", "user")),
            tenant_id=str(data.get("tenant_id", "")),
            workspace_id=str(data.get("workspace_id", "")),
            roles=tuple(data.get("roles") or ()),
            attributes=dict(data.get("attributes") or {}),
            display_name=str(data.get("display_name", "")),
            authn_method=str(data.get("authn_method", "api")),
            active=bool(data.get("active", True)),
            on_behalf_of=(str(data["on_behalf_of"])
                          if data.get("on_behalf_of") else None),
        )


@dataclass(slots=True)
class Tenant:
    """The isolation boundary.  V0.1 uses ``workspace`` as the boundary."""

    id: str
    name: str = ""
    workspace_id: str = ""
    boundary: str = "workspace"
    status: str = "active"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "workspace_id": self.workspace_id,
            "boundary": self.boundary,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Tenant":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            workspace_id=str(data.get("workspace_id", "")),
            boundary=str(data.get("boundary", "workspace")),
            status=str(data.get("status", "active")),
        )


@dataclass(slots=True)
class TenantContext:
    """Request-scoped tenant facts.  Never accept these from a request body."""

    tenant_id: str
    workspace_id: str
    boundary: str = "workspace"

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.workspace_id:
            raise ValidationError("tenant/workspace context must be resolved "
                                  "server-side")
        if self.boundary not in ("workspace", "tenant"):
            raise ValidationError(f"unknown tenant boundary {self.boundary!r}")

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "boundary": self.boundary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TenantContext":
        return cls(
            tenant_id=str(data["tenant_id"]),
            workspace_id=str(data["workspace_id"]),
            boundary=str(data.get("boundary", "workspace")),
        )


@dataclass(slots=True)
class Resource:
    """The object a decision is about.

    ``attributes`` is the record the scope/ABAC filters read.  For action-level
    checks (no concrete record) the PEP supplies at least tenant/workspace.
    """

    type: str
    id: str = ""
    tenant_id: str = ""
    workspace_id: str = ""
    attributes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.type:
            raise ValidationError("resource type must not be empty")

    def record(self) -> dict:
        return {
            "type": self.type,
            "id": self.id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            **dict(self.attributes),
        }

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "id": self.id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Resource":
        return cls(
            type=str(data.get("type") or data.get("resource_type") or "*"),
            id=str(data.get("id") or data.get("resource_id") or ""),
            tenant_id=str(data.get("tenant_id", "")),
            workspace_id=str(data.get("workspace_id", "")),
            attributes=dict(data.get("attributes") or {}),
        )


@dataclass(slots=True)
class Obligation:
    """A machine-checkable duty attached to an allow.

    An obligation the PEP cannot enforce must turn the decision into DENY;
    ``kind`` is intentionally an open set (row_filter / field_mask / ...).
    """

    kind: str
    value: dict = field(default_factory=dict)
    enforced: bool = True

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": dict(self.value),
                "enforced": self.enforced}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Obligation":
        return cls(kind=str(data["kind"]),
                   value=dict(data.get("value") or {}),
                   enforced=bool(data.get("enforced", True)))


@dataclass(slots=True)
class AuthorizationContext:
    """Everything the PDP needs for one decision, resolved server-side."""

    principal: Principal
    tenant: TenantContext
    action: str
    resource: Resource
    request_id: str = ""
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "principal": self.principal.to_dict(),
            "tenant": self.tenant.to_dict(),
            "action": self.action,
            "resource": self.resource.to_dict(),
            "request_id": self.request_id,
            "attributes": dict(self.attributes),
        }


@dataclass(slots=True)
class Decision:
    """PDP output.  ``allow`` is the only field a PEP may branch on."""

    allow: bool
    reason: str
    action: str
    principal_id: str
    tenant_id: str
    workspace_id: str
    decision_id: str = ""
    resource_type: str = ""
    resource_id: str = ""
    policy_id: str = ""
    policy_version: int = 0
    obligations: tuple[Obligation, ...] = ()
    scope: dict | None = None
    created_at: str = ""
    duration_ms: float = 0.0
    ttl_seconds: int = 0

    @property
    def effect(self) -> str:
        return "allow" if self.allow else "deny"

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "effect": self.effect,
            "allow": self.allow,
            "reason": self.reason,
            # Design §3.3 calls this reason_code; keep both spellings so the
            # API and the design vocabulary stay aligned.
            "reason_code": self.reason,
            "action": self.action,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "obligations": [o.to_dict() for o in self.obligations],
            "scope": dict(self.scope) if self.scope else None,
            "created_at": self.created_at,
            "duration_ms": self.duration_ms,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Decision":
        return cls(
            allow=bool(data.get("allow")),
            reason=str(data.get("reason", "")),
            action=str(data.get("action", "")),
            principal_id=str(data.get("principal_id", "")),
            tenant_id=str(data.get("tenant_id", "")),
            workspace_id=str(data.get("workspace_id", "")),
            decision_id=str(data.get("decision_id", "")),
            resource_type=str(data.get("resource_type", "")),
            resource_id=str(data.get("resource_id", "")),
            policy_id=str(data.get("policy_id", "")),
            policy_version=int(data.get("policy_version") or 0),
            obligations=tuple(Obligation.from_dict(o)
                              for o in data.get("obligations") or ()),
            scope=dict(data["scope"]) if data.get("scope") else None,
            created_at=str(data.get("created_at", "")),
            duration_ms=float(data.get("duration_ms") or 0.0),
            ttl_seconds=int(data.get("ttl_seconds") or 0),
        )


@dataclass(slots=True)
class RoleBinding:
    """Subject -> role at a scope.  Custom roles use this path."""

    id: str
    subject_type: str
    subject_id: str
    role: str
    tenant_id: str
    workspace_id: str
    scope_type: str = "workspace"
    scope_id: str = ""
    conditions: dict = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.subject_type not in ("user", "group", "service"):
            raise ValidationError(
                f"unknown binding subject type {self.subject_type!r}")
        if not self.role:
            raise ValidationError("role must not be empty")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "role": self.role,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "conditions": dict(self.conditions),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RoleBinding":
        return cls(
            id=str(data["id"]),
            subject_type=str(data.get("subject_type", "user")),
            subject_id=str(data["subject_id"]),
            role=str(data["role"]),
            tenant_id=str(data.get("tenant_id", "")),
            workspace_id=str(data.get("workspace_id", "")),
            scope_type=str(data.get("scope_type", "workspace")),
            scope_id=str(data.get("scope_id", "")),
            conditions=dict(data.get("conditions") or {}),
            created_at=str(data.get("created_at", "")),
        )


@dataclass(slots=True)
class Group:
    id: str
    name: str
    tenant_id: str
    workspace_id: str
    members: tuple[str, ...] = ()
    created_at: str = ""
    external_id: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "members": list(self.members),
            "created_at": self.created_at,
            "external_id": self.external_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Group":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            tenant_id=str(data.get("tenant_id", "")),
            workspace_id=str(data.get("workspace_id", "")),
            members=tuple(str(m) for m in data.get("members") or ()),
            created_at=str(data.get("created_at", "")),
            external_id=str(data.get("external_id", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass(slots=True)
class RelationTuple:
    """ReBAC edge: ``resource#relation@subject``."""

    resource_type: str
    resource_id: str
    relation: str
    subject_type: str
    subject_id: str
    tenant_id: str
    workspace_id: str
    created_at: str = ""

    def key(self) -> tuple:
        return (self.resource_type, self.resource_id, self.relation,
                self.subject_type, self.subject_id)

    def to_dict(self) -> dict:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "relation": self.relation,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationTuple":
        return cls(
            resource_type=str(data["resource_type"]),
            resource_id=str(data["resource_id"]),
            relation=str(data["relation"]),
            subject_type=str(data.get("subject_type", "user")),
            subject_id=str(data["subject_id"]),
            tenant_id=str(data.get("tenant_id", "")),
            workspace_id=str(data.get("workspace_id", "")),
            created_at=str(data.get("created_at", "")),
        )


@dataclass(slots=True)
class PolicyRecord:
    """Versioned policy document plus its review/publish state."""

    id: str
    workspace_id: str
    tenant_id: str
    version: int
    status: str
    document: dict
    hash: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    approved_by: str = ""
    approved_at: str = ""
    published_at: str = ""
    note: str = ""
    parent_id: str = ""
    validation: dict = field(default_factory=dict)
    #: append-only approval trail: {actor, comment, at}.  ``approved_by`` keeps
    #: the last approver for backwards compatibility; a policy needs
    #: ``required_approvals`` distinct people before it becomes ``approved``.
    approvals: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in POLICY_STATUSES:
            raise ValidationError(f"unknown policy status {self.status!r}")
        if not self.hash:
            self.hash = policy_hash(self.document)

    @property
    def required_approvals(self) -> int:
        approval = (self.document.get("approval") or {})
        raw = approval.get("requiredApprovals",
                           (self.document.get("metadata") or {}).get(
                               "requiredApprovals", 1))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 1
        if approval.get("useRiskTiers"):
            tier = str(approval.get("riskTier") or "").strip().lower() \
                or policy_risk_tier(self.document)
            value = max(value, {"low": 1, "medium": 2, "high": 3}.get(tier, 1))
        return max(1, min(value, 10))

    @property
    def risk_tier(self) -> str:
        approval = (self.document.get("approval") or {})
        explicit = str(approval.get("riskTier")
                       or (self.document.get("metadata") or {}).get(
                           "riskTier") or "").strip().lower()
        if explicit in ("low", "medium", "high"):
            return explicit
        return policy_risk_tier(self.document)

    def approval_progress(self) -> dict:
        actors = [str(item.get("approver") or item.get("actor") or "")
                  for item in self.approvals]
        distinct = list(dict.fromkeys(actor for actor in actors if actor))
        required = self.required_approvals
        return {
            "required": required,
            "received": len(distinct),
            "remaining": max(0, required - len(distinct)),
            "approvers": distinct,
            "satisfied": len(distinct) >= required,
            "riskTier": self.risk_tier,
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "status": self.status,
            "document": dict(self.document),
            "hash": self.hash,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "published_at": self.published_at,
            "note": self.note,
            "parent_id": self.parent_id,
            "validation": dict(self.validation),
            "approvals": [dict(item) for item in self.approvals],
            "approval_progress": self.approval_progress(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PolicyRecord":
        return cls(
            id=str(data["id"]),
            workspace_id=str(data.get("workspace_id", "")),
            tenant_id=str(data.get("tenant_id", "")),
            version=int(data.get("version") or 0),
            status=str(data.get("status", "draft")),
            document=dict(data.get("document") or {}),
            hash=str(data.get("hash", "")),
            created_by=str(data.get("created_by", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            approved_by=str(data.get("approved_by", "")),
            approved_at=str(data.get("approved_at", "")),
            published_at=str(data.get("published_at", "")),
            note=str(data.get("note", "")),
            parent_id=str(data.get("parent_id", "")),
            validation=dict(data.get("validation") or {}),
            approvals=[dict(item) for item in (data.get("approvals") or [])],
        )


@dataclass(slots=True)
class PolicyDecisionRecord:
    """Persisted decision used by the console's "why" panel.

    Decisions are not membership and not audit; they are short-lived evidence
    keyed by workspace and bounded by the caller's query limit.
    """

    decision: Decision

    def to_dict(self) -> dict:
        return self.decision.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PolicyDecisionRecord":
        return cls(Decision.from_dict(data))


def builtin_role_graph() -> dict[str, tuple[str, ...]]:
    return {role: tuple(ROLE_INHERITS[role]) for role in ROLES}
