"""Security service: PIP / PAP / PDP / PEP glue.

This is the only object that reads membership, bindings, groups and policy
documents to answer "may this actor do this?".  It returns a :class:`Decision`;
the application layer either raises ``AccessDeniedError`` or passes the
decision's obligations to the serializer.  The old ``AccessPolicy`` remains the
source of the built-in matrix until a workspace publishes a custom policy.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from ...domain.governance.model import AccessPolicy, Membership
from ...domain.security.filters import (
    evaluate_filter,
    filter_rows,
    normalize_filter,
)
from ...domain.security.model import (
    POLICY_STATUSES,
    ROLE_INHERITS,
    ROLE_RANK,
    ROLES,
    Decision,
    Group,
    PolicyRecord,
    Principal,
    RelationTuple,
    Resource,
    RoleBinding,
    TenantContext,
    policy_hash,
)
from ...domain.security.organization import org_path_of, org_unit_of
from ...errors import (
    AccessDeniedError,
    ConcurrencyConflictError,
    NotFoundError,
    ValidationError,
)
from .masking import FieldMasker, apply_field_masks, validate_mask_spec
from .pdp import PolicyEvaluator
from . import context as auth_context
from .policy import (
    default_policy_document,
    expand_roles,
    policy_diff,
    policy_validation_report,
    role_graph_of,
    validate_policy_document,
)

POLICY_KIND = "security_policy"
#: Evidence retention per workspace.  Pruning happens in batches so a read
#: loop cannot grow the store without bound; the console still shows history.
MAX_DECISIONS_PER_WORKSPACE = 5000
BINDING_KIND = "role_binding"
GROUP_KIND = "security_group"
RELATION_KIND = "relation_tuple"
DECISION_KIND = "authorization_decision"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str = "sec") -> str:
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex[:12]}"


@dataclass(slots=True)
class AuthorizedScope:
    """The PEP's enforcement object for list/read endpoints.

    A result set that cannot be filtered must not be returned.  This wrapper
    makes that explicit: call ``apply`` before serialization.
    """

    decision: Decision
    principal: Principal
    tenant: TenantContext

    def __post_init__(self) -> None:
        if not self.decision.allow:
            return

    def filter(self, records: Iterable[Mapping[str, Any]]) -> list[dict]:
        if not self.decision.allow:
            return []
        rows = [dict(row) for row in records]
        predicate = self.decision.scope
        if predicate:
            rows = filter_rows(rows, predicate, self.principal,
                               self.tenant.to_dict())
        return rows

    def mask(self, records: Iterable[Mapping[str, Any]]) -> list[dict]:
        specs: list[dict] = []
        for obligation in self.decision.obligations:
            if obligation.kind == "field_mask":
                specs.extend(obligation.value.get("fields") or [])
        return apply_field_masks(records, specs)

    def apply(self, records: Iterable[Mapping[str, Any]]) -> list[dict]:
        return self.mask(self.filter(records))

    def ensure(self, record: Mapping[str, Any]) -> dict:
        if not self.filter([record]):
            raise AccessDeniedError(
                f"record is outside the authorized scope for "
                f"{self.decision.action!r}")
        return self.mask([record])[0]


class SecurityService:
    """Authorization kernel used by ``ApplicationContext`` and the console."""

    def __init__(self, repo: Any, audit: Any, *,
                 legacy_policy: AccessPolicy | None = None,
                 clock: Callable[[], str] = _now,
                 id_factory: Callable[[str], str] = _new_id,
                 platform_admins: frozenset[str] = frozenset()) -> None:
        self.repo = repo
        self.audit = audit
        self.legacy = legacy_policy or AccessPolicy()
        self.clock = clock
        self.id_factory = id_factory
        self.platform_admins = frozenset(platform_admins)

    # The PDP is allowed to inspect control-plane rows for the workspace it is
    # evaluating (memberships, bindings, groups, policy, org) even when the
    # caller's active decision belongs to another workspace (e.g. previewing a
    # subject or resolving a principal).  Those reads are explicit-workspace,
    # never unfiltered lists; business data still goes through GovernedRepository.
    def _store(self):
        return getattr(self.repo, "raw", self.repo)

    def _control_get(self, kind: str, workspace_id: str | None,
                     entity_id: str) -> dict | None:
        return self._store().get(kind, workspace_id, entity_id)

    def _control_list(self, kind: str, workspace_id: str) -> list[dict]:
        return self._store().list(kind, workspace_id)

    # ------------------------------------------------------------------
    # Tenant / principal resolution (PIP)
    # ------------------------------------------------------------------

    def tenant_id(self, workspace_id: str) -> str:
        row = self._control_get("workspace", None, workspace_id)
        if row and row.get("tenant_id"):
            return str(row["tenant_id"])
        # V0.1 compatibility: workspace is the tenant boundary, including for
        # workspaces created before tenant_id existed.
        return workspace_id

    def tenant_context(self, workspace_id: str) -> TenantContext:
        return TenantContext(tenant_id=self.tenant_id(workspace_id),
                             workspace_id=workspace_id,
                             boundary="workspace")

    def _membership_roles(self, actor: str, workspace_id: str) -> list[str]:
        roles: list[str] = []
        for row in self._control_list("membership", workspace_id):
            if row.get("user") == actor and row.get("role"):
                roles.append(str(row["role"]))
        return roles

    def _group_closure(self, actor: str, workspace_id: str) -> set[str]:
        groups = [Group.from_dict(row) for row in
                  self._control_list(GROUP_KIND, workspace_id)]
        found: set[str] = {group.id for group in groups if actor in group.members}
        changed = True
        while changed:
            changed = False
            for group in groups:
                if group.id not in found and any(member in found
                                                 for member in group.members):
                    found.add(group.id)
                    changed = True
        return found

    def roles_for(self, actor: str, workspace_id: str,
                  attributes: dict | None = None) -> list[str]:
        """Direct roles from legacy membership + role bindings + groups."""
        roles = list(dict.fromkeys(self._membership_roles(actor, workspace_id)))
        groups = self._group_closure(actor, workspace_id)
        tenant = self.tenant_context(workspace_id)
        base_principal = Principal(
            id=actor, tenant_id=tenant.tenant_id,
            workspace_id=workspace_id, roles=tuple(roles),
            attributes=dict(attributes or {}))
        resource = Resource(type="workspace", id=workspace_id,
                            tenant_id=tenant.tenant_id,
                            workspace_id=workspace_id).record()
        for row in self._control_list(BINDING_KIND, workspace_id):
            binding = RoleBinding.from_dict(row)
            if binding.scope_type == "resource":
                # Resource-scoped grants live in RelationTuple, not in a flat
                # workspace role.  Ignoring them here prevents a narrow grant
                # from silently becoming a workspace-wide role.
                continue
            if (binding.scope_type == "tenant"
                    and binding.scope_id not in (tenant.tenant_id, workspace_id)):
                continue
            if binding.subject_type == "user":
                if binding.subject_id != actor:
                    continue
            elif binding.subject_type == "group":
                if binding.subject_id not in groups:
                    continue
            else:
                continue
            if binding.conditions and not evaluate_filter(
                    binding.conditions, base_principal, tenant.to_dict(),
                    resource):
                continue
            roles.append(binding.role)
        return list(dict.fromkeys(role for role in roles if role))

    def principal(self, actor: str, workspace_id: str, *,
                  kind: str = "user", attributes: dict | None = None,
                  on_behalf_of: str | None = None,
                  authn_method: str = "api") -> Principal:
        tenant_id = self.tenant_id(workspace_id)
        attrs = dict(attributes or {})
        # Organization membership is resolved from storage, never accepted
        # from the caller: a forged org_path would widen every row scope.
        try:
            units = self._control_list("org_unit", workspace_id)
        except Exception:  # pragma: no cover - storage is authoritative
            units = []
        path = org_path_of(units, actor)
        if path:
            attrs["org_path"] = path
            unit = org_unit_of(units, actor)
            if unit is not None:
                attrs["org_unit"] = unit.id
        attrs["platform_admin"] = actor in self.platform_admins
        return Principal(
            id=actor,
            kind=kind,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            roles=tuple(self.roles_for(actor, workspace_id,
                                       attributes=attrs)),
            attributes=attrs,
            on_behalf_of=on_behalf_of,
            authn_method=authn_method,
        )

    def agent_principal(self, actor: str, workspace_id: str, *,
                        agent_id: str = "agent") -> Principal:
        """Agent acts *as* the caller and never gains roles the caller lacks."""
        caller = self.principal(actor, workspace_id)
        return Principal(
            id=agent_id,
            kind="agent",
            tenant_id=caller.tenant_id,
            workspace_id=workspace_id,
            roles=caller.roles,
            attributes={"caller": actor,
                        "platform_admin": caller.attributes.get("platform_admin",
                                                                False)},
            on_behalf_of=actor,
        )

    # ------------------------------------------------------------------
    # PEP checks used by ApplicationContext
    # ------------------------------------------------------------------

    def check_role(self, actor: str, workspace_id: str,
                   required: str = "viewer") -> None:
        if required not in ROLE_RANK:
            raise AccessDeniedError(f"unknown role requirement {required!r}")

        def _granted(reason: str = "role_check") -> None:
            # Legacy role PEPs still need a request scope for the storage
            # boundary; synthesize an action-shaped ALLOW (no row scope).
            active = self.active_policy(workspace_id)
            decision = Decision(
                allow=True, reason=reason, action=f"role.{required}",
                decision_id=self.id_factory("dec"),
                created_at=self.clock(),
                principal_id=actor, tenant_id=self.tenant_id(workspace_id),
                workspace_id=workspace_id, policy_id=active.id,
                policy_version=active.version,
            )
            principal = self.principal(actor, workspace_id)
            auth_context.bind(decision, principal,
                              self.tenant_context(workspace_id))
            # Every allow is evidence, including legacy role checks.  A
            # logging failure must never turn an allow into a request error.
            try:
                self.record_decision(decision)
            except Exception:  # pragma: no cover - defensive
                pass

        if actor in self.platform_admins and required == "viewer":
            _granted("platform_admin_auditor")
            return  # cross-workspace read-only auditor exception
        roles = self.roles_for(actor, workspace_id)
        if required == "viewer" and actor in self.platform_admins:
            _granted("platform_admin_auditor")
            return
        graph = self._active_role_graph(workspace_id)
        granted = expand_roles(roles, graph) if graph else set(roles)
        rank_needed = ROLE_RANK[required]
        best = max((ROLE_RANK.get(role, -1) for role in granted), default=-1)
        if best >= rank_needed:
            _granted()
            return
        if not roles:
            raise AccessDeniedError(
                f"user {actor!r} is not a member of workspace "
                f"{workspace_id!r}")
        rank = max((ROLE_RANK.get(role, -1) for role in roles), default=-1)
        if rank < 0:
            raise AccessDeniedError(
                f"user {actor!r} has no role in workspace {workspace_id!r} "
                f"that satisfies {required!r}")
        raise AccessDeniedError(
            f"user {actor!r} has role {max(roles, key=lambda r: ROLE_RANK.get(r, -1))!r} "
            f"in workspace {workspace_id!r}; {required!r} is required")

    def check_action(self, actor: str, workspace_id: str, action: str,
                     *, record_denials: bool = True,
                     record: bool | None = None,
                     bind_context: bool = True) -> Decision:
        """Authorize an action and attach the decision to this execution.

        Every HTTP/use-case check is evidence: allow and deny are persisted by
        default so an auditor can reconstruct not only who was blocked but also
        why a read succeeded.  Capability probes pass ``record_denials=False``
        (and ``record=False``) to avoid inflating the decision log.
        """
        do_record = record_denials if record is None else bool(record)
        entry = self.legacy.ACTION_POLICY.get(action)
        resource_type = entry[1] if entry else action.rsplit(".", 1)[0]
        principal = self.principal(actor, workspace_id)
        tenant = self.tenant_context(workspace_id)
        resource = Resource(type=resource_type, id=workspace_id,
                            tenant_id=principal.tenant_id,
                            workspace_id=workspace_id)
        decision = self.decision_for(principal, tenant, action, resource,
                                     bind_context=bind_context)
        # The storage PEP and the audit writer read this context; bind even a
        # DENY so downstream code cannot accidentally use a stale ALLOW.
        if bind_context:
            auth_context.bind(decision, principal, tenant)
        if do_record:
            try:
                self.record_decision(decision)
            except Exception:  # pragma: no cover - logging must not flip deny
                pass
        if not decision.allow:
            raise AccessDeniedError(
                f"action {action!r} denied for user {actor!r} in workspace "
                f"{workspace_id!r}: {decision.reason}")
        return decision

    def decision_for(self, principal: Principal, tenant: TenantContext,
                     action: str, resource: Resource, *,
                     policy: PolicyRecord | None = None,
                     record: bool = False,
                     check_scope: bool = True,
                     bind_context: bool = True) -> Decision:
        record_obj = policy or self.active_policy(tenant.workspace_id)
        evaluator = PolicyEvaluator(record_obj)
        decision = evaluator.evaluate(
            principal, tenant, action, resource,
            relation_reader=self.relation_reader(tenant.workspace_id),
            check_scope=check_scope,
        )
        decision.decision_id = self.id_factory("dec")
        decision.created_at = self.clock()
        if bind_context:
            auth_context.bind(decision, principal, tenant)
        if record:
            self.record_decision(decision)
        return decision

    # ------------------------------------------------------------------
    # Policy store (PAP)
    # ------------------------------------------------------------------

    def _policy_records(self, workspace_id: str) -> list[PolicyRecord]:
        return [PolicyRecord.from_dict(row) for row in
                self._control_list(POLICY_KIND, workspace_id)]

    def active_policy(self, workspace_id: str) -> PolicyRecord:
        published = [record for record in self._policy_records(workspace_id)
                     if record.status == "published"]
        if published:
            return max(published, key=lambda record: (record.version,
                                                      record.published_at,
                                                      record.id))
        return self.builtin_policy(workspace_id)

    def builtin_policy(self, workspace_id: str) -> PolicyRecord:
        document = default_policy_document(self.legacy)
        return PolicyRecord(
            id="builtin",
            workspace_id=workspace_id,
            tenant_id=self.tenant_id(workspace_id),
            version=0,
            status="published",
            document=document,
            hash=policy_hash(document),
            created_at="",
            updated_at="",
            note="built-in policy generated from the legacy action matrix",
        )

    def list_policies(self, workspace_id: str) -> dict:
        records = sorted(self._policy_records(workspace_id),
                         key=lambda record: (record.version, record.created_at),
                         reverse=True)
        active = self.active_policy(workspace_id)
        return {
            "policies": [record.to_dict() for record in records],
            "active": active.to_dict(),
            "builtin": active.id == "builtin",
        }

    def get_policy(self, workspace_id: str, policy_id: str) -> PolicyRecord:
        if policy_id == "builtin":
            return self.builtin_policy(workspace_id)
        row = self._control_get(POLICY_KIND, workspace_id, policy_id)
        if row is None:
            raise NotFoundError(f"security policy not found: {policy_id!r}")
        return PolicyRecord.from_dict(row)

    def _check_version(self, record: PolicyRecord,
                       expected_version: int | None) -> None:
        if expected_version is None:
            return
        try:
            expected = int(expected_version)
        except (TypeError, ValueError) as exc:
            raise ValidationError("expected_version must be an integer") from exc
        if record.version != expected:
            raise ConcurrencyConflictError(
                f"policy {record.id!r} is at version {record.version}, "
                f"expected {expected}")

    def create_policy(self, workspace_id: str, actor: str, *,
                      document: dict | None = None, note: str = "") -> PolicyRecord:
        if document is None:
            document = default_policy_document(self.legacy)
        document = deepcopy(document)
        report = validate_policy_document(document)
        now = self.clock()
        record = PolicyRecord(
            id=self.id_factory("pol"),
            workspace_id=workspace_id,
            tenant_id=self.tenant_id(workspace_id),
            version=0,
            status="draft",
            document=document,
            hash=report["hash"],
            created_by=actor,
            created_at=now,
            updated_at=now,
            note=note,
            validation=report,
        )
        self.repo.put(POLICY_KIND, workspace_id, record.id, record.to_dict())
        self._audit(actor, workspace_id, "security.policy_created", record.id,
                    {"hash": record.hash, "note": note})
        return record

    def update_policy_draft(self, workspace_id: str, policy_id: str, actor: str,
                            document: dict, note: str = "",
                            expected_version: int | None = None) -> PolicyRecord:
        record = self.get_policy(workspace_id, policy_id)
        if record.status in ("published", "deprecated"):
            raise ValidationError("published policy versions are immutable")
        self._check_version(record, expected_version)
        document = deepcopy(document)
        report = validate_policy_document(document)
        changed = record.hash != report["hash"]
        if changed:
            # Approvals are bound to the bytes that were reviewed.  Editing a
            # validated/approved draft must invalidate every signature,
            # otherwise N-of-M could be downgraded to a single fresh approval.
            record.approvals = []
            record.approved_by = ""
            record.approved_at = ""
        record.document = document
        record.hash = report["hash"]
        record.validation = report
        record.status = "validated" if report["valid"] else "draft"
        record.updated_at = self.clock()
        record.note = note or record.note
        self.repo.put(POLICY_KIND, workspace_id, record.id, record.to_dict())
        self._audit(actor, workspace_id, "security.policy_updated", record.id,
                    {"hash": record.hash})
        return record

    def validate_policy(self, workspace_id: str, policy_id: str,
                        actor: str) -> PolicyRecord:
        record = self.get_policy(workspace_id, policy_id)
        if record.status in ("published", "deprecated"):
            raise ValidationError("published policy versions are immutable")
        report = policy_validation_report(record.document)
        record.validation = report
        record.status = "validated" if report["valid"] else "draft"
        record.updated_at = self.clock()
        self.repo.put(POLICY_KIND, workspace_id, record.id, record.to_dict())
        self._audit(actor, workspace_id, "security.policy_validated", record.id,
                    {"valid": report["valid"],
                     "errors": report["errors"][:8]})
        if not report["valid"]:
            raise ValidationError(
                "invalid security policy: " + "; ".join(report["errors"]))
        return record

    def approve_policy(self, workspace_id: str, policy_id: str, actor: str,
                       comment: str = "") -> PolicyRecord:
        record = self.get_policy(workspace_id, policy_id)
        if record.status not in ("validated", "approved"):
            raise ValidationError("policy must be validated before approval")
        if record.created_by == actor:
            raise ValidationError(
                "separation of duties: the author cannot approve their own "
                "policy")
        if any(item.get("actor") == actor for item in record.approvals):
            raise ValidationError(
                "this approver has already signed this policy version")
        now = self.clock()
        record.approvals = [*record.approvals,
                            {"actor": actor, "comment": comment, "at": now}]
        progress = record.approval_progress()
        record.validation = {
            **record.validation, "approvalComment": comment,
            "approvalProgress": progress,
        }
        if not progress["satisfied"]:
            # Still validated: an N-of-M policy is not publishable until every
            # required reviewer has signed.  The caller sees progress in the
            # returned record instead of a misleading "approved" status.
            record.updated_at = now
            self.repo.put(POLICY_KIND, workspace_id, record.id,
                          record.to_dict())
            self._audit(actor, workspace_id, "security.policy_approval_recorded",
                        record.id, {"author": record.created_by,
                                    "comment": comment,
                                    "progress": progress})
            return record
        record.status = "approved"
        record.approved_by = actor
        record.approved_at = now
        record.updated_at = now
        self.repo.put(POLICY_KIND, workspace_id, record.id, record.to_dict())
        self._audit(actor, workspace_id, "security.policy_approved", record.id,
                    {"author": record.created_by, "comment": comment,
                     "approvers": progress["approvers"]})
        return record

    def publish_policy(self, workspace_id: str, policy_id: str,
                       actor: str,
                       expected_version: int | None = None) -> PolicyRecord:
        record = self.get_policy(workspace_id, policy_id)
        self._check_version(record, expected_version)
        if record.status != "approved":
            raise ValidationError("policy must be approved before publishing")
        if not record.approved_by:
            raise ValidationError("policy has no recorded approver")
        version = max((item.version for item in self._policy_records(workspace_id)),
                      default=0) + 1
        now = self.clock()
        for current in self._policy_records(workspace_id):
            if current.status == "published":
                current.status = "deprecated"
                current.updated_at = now
                self.repo.put(POLICY_KIND, workspace_id, current.id,
                              current.to_dict())
        record.version = version
        record.status = "published"
        record.published_at = now
        record.updated_at = now
        record.hash = policy_hash(record.document)
        self.repo.put(POLICY_KIND, workspace_id, record.id, record.to_dict())
        self._audit(actor, workspace_id, "security.policy_published", record.id,
                    {"version": version, "hash": record.hash,
                     "approved_by": record.approved_by})
        return record

    def rollback_policy(self, workspace_id: str, policy_id: str,
                        actor: str,
                        expected_version: int | None = None) -> PolicyRecord:
        active = self.active_policy(workspace_id)
        self._check_version(active, expected_version)
        old = self.get_policy(workspace_id, policy_id)
        if old.status not in ("deprecated", "published"):
            raise ValidationError(
                "only a previously published policy can be rolled back to")
        version = max((item.version for item in self._policy_records(workspace_id)),
                      default=0) + 1
        now = self.clock()
        for current in self._policy_records(workspace_id):
            if current.status == "published":
                current.status = "deprecated"
                current.updated_at = now
                self.repo.put(POLICY_KIND, workspace_id, current.id,
                              current.to_dict())
        record = PolicyRecord(
            id=self.id_factory("pol"),
            workspace_id=workspace_id,
            tenant_id=self.tenant_id(workspace_id),
            version=version,
            status="published",
            document=deepcopy(old.document),
            created_by=actor,
            created_at=now,
            updated_at=now,
            approved_by=old.approved_by or actor,
            approved_at=old.approved_at or now,
            published_at=now,
            note=f"rollback to {old.id} (version {old.version})",
            parent_id=old.id,
            validation=old.validation,
        )
        record.hash = policy_hash(record.document)
        self.repo.put(POLICY_KIND, workspace_id, record.id, record.to_dict())
        self._audit(actor, workspace_id, "security.policy_rolled_back", record.id,
                    {"from": old.id, "version": version})
        return record

    def policy_diff(self, workspace_id: str, policy_id: str) -> dict:
        target = self.get_policy(workspace_id, policy_id)
        active = self.active_policy(workspace_id)
        return {
            "policy": target.id,
            "against": active.id,
            **policy_diff(active.document, target.document),
        }

    def _active_role_graph(self, workspace_id: str) -> dict[str, tuple[str, ...]]:
        try:
            return role_graph_of(self.active_policy(workspace_id).document)
        except Exception:  # pragma: no cover - corrupt policy is denied elsewhere
            return {role: tuple(ROLE_INHERITS[role]) for role in ROLES}

    # ------------------------------------------------------------------
    # Bindings, groups, relations (PIP / ReBAC)
    # ------------------------------------------------------------------

    def list_bindings(self, workspace_id: str) -> list[dict]:
        return sorted(self._control_list(BINDING_KIND, workspace_id),
                      key=lambda row: (row.get("subject_type", ""),
                                       row.get("subject_id", ""),
                                       row.get("role", "")))

    def create_binding(self, workspace_id: str, actor: str, *,
                       subject_type: str, subject_id: str, role: str,
                       scope_type: str = "workspace", scope_id: str = "",
                       conditions: dict | None = None) -> dict:
        document = self.active_policy(workspace_id).document
        known_roles = set(role_graph_of(document))
        if role not in known_roles:
            raise ValidationError(f"unknown role {role!r} in active policy")
        if subject_type not in ("user", "group", "service"):
            raise ValidationError(f"unknown binding subject type "
                                  f"{subject_type!r}")
        if not str(subject_id).strip():
            raise ValidationError("binding subject_id must not be empty")
        if scope_type not in ("workspace", "tenant"):
            raise ValidationError(
                f"unsupported binding scope type {scope_type!r}; "
                "use relation tuples for resource-scoped grants")
        normalized_conditions = normalize_filter(conditions) if conditions else {}
        now = self.clock()
        if scope_type == "tenant":
            resolved_scope_id = scope_id or self.tenant_id(workspace_id)
        else:
            resolved_scope_id = workspace_id
        binding = RoleBinding(
            id=self.id_factory("rb"),
            subject_type=subject_type,
            subject_id=subject_id,
            role=role,
            tenant_id=self.tenant_id(workspace_id),
            workspace_id=workspace_id,
            scope_type=scope_type,
            scope_id=resolved_scope_id,
            conditions=normalized_conditions,
            created_at=now,
        )
        self.repo.put(BINDING_KIND, workspace_id, binding.id, binding.to_dict())
        self._audit(actor, workspace_id, "security.binding_created", binding.id,
                    binding.to_dict())
        return binding.to_dict()

    def delete_binding(self, workspace_id: str, binding_id: str,
                       actor: str) -> dict:
        row = self._control_get(BINDING_KIND, workspace_id, binding_id)
        if row is None:
            raise NotFoundError(f"role binding not found: {binding_id!r}")
        self.repo.delete(BINDING_KIND, workspace_id, binding_id)
        self._audit(actor, workspace_id, "security.binding_deleted", binding_id,
                    {"subject_id": row.get("subject_id"),
                     "role": row.get("role")})
        return {"id": binding_id, "deleted": True}

    def list_groups(self, workspace_id: str) -> list[dict]:
        return sorted(self._control_list(GROUP_KIND, workspace_id),
                      key=lambda row: row.get("name", ""))

    def create_group(self, workspace_id: str, actor: str, name: str,
                     members: Iterable[str] = ()) -> dict:
        if not str(name).strip():
            raise ValidationError("group name must not be empty")
        group = Group(
            id=self.id_factory("grp"),
            name=str(name).strip(),
            tenant_id=self.tenant_id(workspace_id),
            workspace_id=workspace_id,
            members=tuple(dict.fromkeys(str(member) for member in members)),
            created_at=self.clock(),
        )
        self.repo.put(GROUP_KIND, workspace_id, group.id, group.to_dict())
        self._audit(actor, workspace_id, "security.group_created", group.id,
                    group.to_dict())
        return group.to_dict()

    def update_group(self, workspace_id: str, actor: str, group_id: str, *,
                     name: str | None = None,
                     members: Iterable[str] | None = None) -> dict:
        row = self._control_get(GROUP_KIND, workspace_id, group_id)
        if row is None:
            raise NotFoundError(f"group not found: {group_id!r}")
        group = Group.from_dict(row)
        if name is not None:
            if not str(name).strip():
                raise ValidationError("group name must not be empty")
            group.name = str(name).strip()
        if members is not None:
            group.members = tuple(dict.fromkeys(str(member)
                                                for member in members))
        self.repo.put(GROUP_KIND, workspace_id, group.id, group.to_dict())
        self._audit(actor, workspace_id, "security.group_updated", group.id,
                    {"name": group.name, "members": list(group.members)})
        return group.to_dict()

    def delete_group(self, workspace_id: str, actor: str, group_id: str) -> dict:
        row = self._control_get(GROUP_KIND, workspace_id, group_id)
        if row is None:
            raise NotFoundError(f"group not found: {group_id!r}")
        self.repo.delete(GROUP_KIND, workspace_id, group_id)
        self._audit(actor, workspace_id, "security.group_deleted", group_id,
                    {"name": row.get("name")})
        return {"id": group_id, "deleted": True}

    def list_relations(self, workspace_id: str) -> list[dict]:
        return sorted(self._control_list(RELATION_KIND, workspace_id),
                      key=lambda row: (row.get("resource_type", ""),
                                       row.get("resource_id", ""),
                                       row.get("relation", ""),
                                       row.get("subject_id", "")))

    def create_relation(self, workspace_id: str, actor: str, *,
                        resource_type: str, resource_id: str, relation: str,
                        subject_type: str = "user",
                        subject_id: str = "") -> dict:
        for value, label in ((resource_type, "resource_type"),
                             (resource_id, "resource_id"),
                             (relation, "relation"),
                             (subject_id, "subject_id")):
            if not str(value).strip():
                raise ValidationError(f"relation {label} must not be empty")
        if subject_type not in ("user", "group"):
            raise ValidationError(f"unknown relation subject type "
                                  f"{subject_type!r}")
        now = self.clock()
        tuple_row = RelationTuple(
            resource_type=resource_type,
            resource_id=resource_id,
            relation=relation,
            subject_type=subject_type,
            subject_id=subject_id,
            tenant_id=self.tenant_id(workspace_id),
            workspace_id=workspace_id,
            created_at=now,
        )
        self.repo.put(RELATION_KIND, workspace_id,
                      self._relation_key(tuple_row), tuple_row.to_dict())
        self._audit(actor, workspace_id, "security.relation_created",
                    self._relation_key(tuple_row), tuple_row.to_dict())
        return tuple_row.to_dict()

    def delete_relation(self, workspace_id: str, actor: str, *,
                        resource_type: str, resource_id: str, relation: str,
                        subject_type: str = "user",
                        subject_id: str = "") -> dict:
        key = f"{resource_type}:{resource_id}:{relation}:" \
              f"{subject_type}:{subject_id}"
        if not self.repo.delete(RELATION_KIND, workspace_id, key):
            raise NotFoundError(f"relation not found: {key!r}")
        self._audit(actor, workspace_id, "security.relation_deleted", key, {})
        return {"id": key, "deleted": True}

    @staticmethod
    def _relation_key(tuple_row: RelationTuple) -> str:
        return ":".join(str(part) for part in tuple_row.key())

    def relation_reader(self, workspace_id: str):
        tuples = [RelationTuple.from_dict(row) for row in
                  self._control_list(RELATION_KIND, workspace_id)]
        groups = {group.id: group for group in
                  (Group.from_dict(row) for row in
                   self._control_list(GROUP_KIND, workspace_id))}

        def member_of(subject_id: str, group_id: str,
                      seen: set[str] | None = None) -> bool:
            seen = seen or set()
            if group_id in seen:
                return False
            seen.add(group_id)
            group = groups.get(group_id)
            if group is None:
                return False
            if subject_id in group.members:
                return True
            return any(member_of(subject_id, member, seen)
                       for member in group.members
                       if member in groups)

        def check(resource_type: str, resource_id: str, relation: str,
                  subject_id: str) -> bool:
            for tuple_row in tuples:
                if (tuple_row.resource_type != resource_type
                        or tuple_row.resource_id != resource_id
                        or tuple_row.relation != relation):
                    continue
                if tuple_row.subject_type == "user" \
                        and tuple_row.subject_id == subject_id:
                    return True
                if tuple_row.subject_type == "group" \
                        and member_of(subject_id, tuple_row.subject_id):
                    return True
            return False

        return check

    # ------------------------------------------------------------------
    # Decision evidence and PEP helpers
    # ------------------------------------------------------------------

    def record_decision(self, decision: Decision) -> None:
        if not decision.decision_id:
            decision.decision_id = self.id_factory("dec")
        if not decision.created_at:
            decision.created_at = self.clock()
        self.repo.put(DECISION_KIND, decision.workspace_id,
                      decision.decision_id, decision.to_dict())
        # Evidence is retained, not unbounded: keep the newest N decisions per
        # workspace so an always-allow read loop cannot grow the store forever.
        rows = self._control_list(DECISION_KIND, decision.workspace_id)
        if len(rows) > MAX_DECISIONS_PER_WORKSPACE + 100:
            rows.sort(key=lambda row: (row.get("created_at", ""),
                                       row.get("decision_id", "")))
            for stale in rows[:-MAX_DECISIONS_PER_WORKSPACE]:
                self.repo.delete(DECISION_KIND, decision.workspace_id,
                                 str(stale.get("decision_id") or stale.get("id")))

    def list_decisions(self, workspace_id: str, *,
                       limit: int = 50) -> list[dict]:
        rows = sorted(self._control_list(DECISION_KIND, workspace_id),
                      key=lambda row: (row.get("created_at", ""),
                                       row.get("decision_id", "")),
                      reverse=True)
        return rows[:max(1, min(int(limit or 50), 500))]

    def get_decision(self, workspace_id: str, decision_id: str) -> dict:
        row = self._control_get(DECISION_KIND, workspace_id, decision_id)
        if row is None:
            raise NotFoundError(
                f"authorization decision not found: {decision_id!r}")
        return row

    def roles_view(self, workspace_id: str) -> dict:
        policy = self.active_policy(workspace_id)
        bindings = self.list_bindings(workspace_id)
        counts: dict[str, int] = {}
        for binding in bindings:
            role = str(binding.get("role") or "")
            counts[role] = counts.get(role, 0) + 1
        return {
            "roles": [
                {**dict(role), "bindings": counts.get(role.get("id"), 0)}
                for role in policy.document.get("roles") or ()
                if isinstance(role, Mapping)
            ],
            "policy_id": policy.id,
            "policy_version": policy.version,
        }

    def scopes_view(self, workspace_id: str) -> dict:
        policy = self.active_policy(workspace_id)
        return {
            "scopes": dict(policy.document.get("scopes") or {}),
            "policy_id": policy.id,
            "policy_version": policy.version,
        }

    def authorized_scope(self, decision: Decision, principal: Principal,
                         tenant: TenantContext) -> AuthorizedScope:
        return AuthorizedScope(decision=decision, principal=principal,
                               tenant=tenant)

    def redact(self, decision: Decision, records: Iterable[Mapping[str, Any]]
               ) -> list[dict]:
        specs: list[dict] = []
        for obligation in decision.obligations:
            if obligation.kind == "field_mask":
                specs.extend(obligation.value.get("fields") or [])
        return apply_field_masks(records, specs)

    def filter_objects(self, workspace_id: str, actor: str,
                       objects: Iterable[Mapping[str, Any]], *,
                       action: str = "object.read",
                       resource_type: str = "object",
                       attributes: dict | None = None) -> list[dict]:
        """Row scope + field masks for kernel object rows (PEP helper).

        Mirrors ``SecurityUseCases.filter_objects`` for callers that only hold
        the kernel service (agent observations, natural-language answers).
        """
        from copy import deepcopy

        from ...domain.security.model import Resource

        self.check_action(actor, workspace_id, "workspace.read")
        principal = self.principal(actor, workspace_id, attributes=attributes)
        tenant = self.tenant_context(workspace_id)
        decision = self.decision_for(
            principal, tenant, action,
            Resource(type=resource_type, id="",
                     tenant_id=tenant.tenant_id,
                     workspace_id=workspace_id),
            check_scope=False)
        if not decision.allow:
            from ...errors import AccessDeniedError

            raise AccessDeniedError(
                f"action {action!r} denied on {resource_type!r}: "
                f"{decision.reason}")
        scope = self.authorized_scope(decision, principal, tenant)
        masks: list[dict] = []
        for obligation in decision.obligations:
            if obligation.kind == "field_mask":
                masks.extend(obligation.value.get("fields") or [])
        kept: list[dict] = []
        for obj in objects:
            props = dict(obj.get("properties") or {})
            flat = {**props, **dict(obj), "tenant_id": tenant.tenant_id,
                    "workspace_id": workspace_id}
            if not scope.filter([flat]):
                continue
            masked = apply_field_masks([flat], masks)[0] if masks else flat
            out = deepcopy(dict(obj))
            out_props = dict(out.get("properties") or {})
            for key in list(out_props):
                if key in masked:
                    out_props[key] = masked[key]
            out["properties"] = out_props
            kept.append(out)
        return kept

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _audit(self, actor: str, workspace_id: str, action: str, target: str,
               detail: dict | None = None) -> None:
        active = self.active_policy(workspace_id)
        self.audit.append({
            "ts": self.clock(),
            "actor": actor,
            "workspace_id": workspace_id,
            "tenant_id": self.tenant_id(workspace_id),
            "action": action,
            "target": target,
            "detail": {
                **(detail or {}),
                "policy_id": active.id,
                "policy_version": active.version,
            },
        })


__all__ = [
    "AuthorizedScope",
    "BINDING_KIND",
    "DECISION_KIND",
    "GROUP_KIND",
    "POLICY_KIND",
    "RELATION_KIND",
    "SecurityService",
]
