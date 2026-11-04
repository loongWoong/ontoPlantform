"""Use cases for the permission console and the security kernel API.

All entry points re-authorize the caller through the same PEP
(``ApplicationContext.require_action``), including preview and dry-run.  An
operator who cannot manage security cannot inspect draft policies; a viewer may
preview a decision only within their own workspace.
"""

from __future__ import annotations

from typing import Any, Mapping

from ...domain.security.model import Resource
from ...errors import AccessDeniedError, ValidationError
from .service import SecurityService


def _resource(workspace_id: str, tenant_id: str, payload: Mapping[str, Any],
              *, default_type: str = "workspace") -> Resource:
    resource_type = str(payload.get("resource_type")
                        or payload.get("type") or default_type)
    return Resource(
        type=resource_type,
        id=str(payload.get("resource_id") or payload.get("id") or ""),
        # Tenant/workspace are always resolved server-side, never accepted from
        # the request body (design §6.2).
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        attributes=dict(payload.get("attributes") or {}),
    )


def _can(ctx, actor: str, workspace_id: str, action: str) -> bool:
    """Non-throwing capability probe for the console's button state."""
    try:
        ctx.security.check_action(actor, workspace_id, action,
                                  record_denials=False, record=False,
                                  bind_context=False)
        return True
    except AccessDeniedError:
        return False


class SecurityUseCases:
    def __init__(self, ctx: Any, service: SecurityService) -> None:
        self._ctx = ctx
        self.service = service

    # ---- decision preview -------------------------------------------------

    def authorize(self, workspace_id: str, actor: str, action: str, *,
                  resource_type: str = "workspace", resource_id: str = "",
                  attributes: dict | None = None,
                  subject: str | None = None,
                  record: bool = True) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        if not action:
            raise ValidationError("action is required")
        tenant = self.service.tenant_context(workspace_id)
        # The caller is authorized above; ``subject`` only changes whose
        # perspective is evaluated, never who is allowed to ask.
        principal = self.service.principal(subject or actor, workspace_id)
        resource = Resource(type=resource_type, id=resource_id,
                            tenant_id=tenant.tenant_id,
                            workspace_id=workspace_id,
                            attributes=attributes or {})
        decision = self.service.decision_for(principal, tenant, action,
                                             resource, record=record)
        return {
            "decision": decision.to_dict(),
            "principal": principal.to_dict(),
            "tenant": tenant.to_dict(),
        }

    # ---- policy lifecycle -------------------------------------------------

    def policies(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.list_policies(workspace_id)

    def policy(self, workspace_id: str, actor: str, policy_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.get_policy(workspace_id, policy_id).to_dict()

    def create_policy(self, workspace_id: str, actor: str, *,
                      document: dict | None = None, note: str = "") -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.create_policy(workspace_id, actor,
                                          document=document, note=note).to_dict()

    def update_policy(self, workspace_id: str, actor: str, policy_id: str,
                      document: dict, note: str = "",
                      expected_version: int | None = None) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.update_policy_draft(
            workspace_id, policy_id, actor, document, note=note,
            expected_version=expected_version).to_dict()

    def validate_policy(self, workspace_id: str, actor: str,
                        policy_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.validate_policy(
            workspace_id, policy_id, actor).to_dict()

    def approve_policy(self, workspace_id: str, actor: str, policy_id: str,
                       comment: str = "") -> dict:
        self._ctx.require_action(actor, workspace_id, "security.publish")
        return self.service.approve_policy(workspace_id, policy_id, actor,
                                          comment=comment).to_dict()

    def publish_policy(self, workspace_id: str, actor: str,
                       policy_id: str,
                       expected_version: int | None = None) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.publish")
        return self.service.publish_policy(
            workspace_id, policy_id, actor,
            expected_version=expected_version).to_dict()

    def rollback_policy(self, workspace_id: str, actor: str,
                        policy_id: str,
                        expected_version: int | None = None) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.publish")
        return self.service.rollback_policy(
            workspace_id, policy_id, actor,
            expected_version=expected_version).to_dict()

    def diff_policy(self, workspace_id: str, actor: str, policy_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.policy_diff(workspace_id, policy_id)

    def dry_run(self, workspace_id: str, actor: str, policy_id: str, *,
                action: str, resource_type: str = "workspace",
                resource_id: str = "",
                attributes: dict | None = None,
                subject: str | None = None) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        record = self.service.get_policy(workspace_id, policy_id)
        tenant = self.service.tenant_context(workspace_id)
        # dry-run is a policy-impact tool: an administrator may evaluate the
        # target policy as another workspace subject without impersonating it.
        principal = self.service.principal(subject or actor, workspace_id)
        resource = Resource(type=resource_type, id=resource_id,
                            tenant_id=tenant.tenant_id,
                            workspace_id=workspace_id,
                            attributes=attributes or {})
        decision = self.service.decision_for(
            principal, tenant, action, resource, policy=record,
            bind_context=False)
        return {
            "policy_id": record.id,
            "policy_version": record.version,
            "subject": principal.id,
            "decision": decision.to_dict(),
            "validation": record.validation,
        }

    # ---- bindings / groups / relations ------------------------------------

    def bindings(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.list_bindings(workspace_id)

    def create_binding(self, workspace_id: str, actor: str, body: dict) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.create_binding(
            workspace_id, actor,
            subject_type=str(body.get("subject_type") or "user"),
            subject_id=str(body.get("subject_id") or body.get("user") or ""),
            role=str(body.get("role") or ""),
            scope_type=str(body.get("scope_type") or "workspace"),
            scope_id=str(body.get("scope_id") or ""),
            conditions=body.get("conditions"),
        )

    def delete_binding(self, workspace_id: str, actor: str,
                       binding_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.delete_binding(workspace_id, binding_id, actor)

    def groups(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.list_groups(workspace_id)

    def create_group(self, workspace_id: str, actor: str, body: dict) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        members = body.get("members") or []
        if not isinstance(members, list):
            raise ValidationError("group members must be a list")
        return self.service.create_group(
            workspace_id, actor, str(body.get("name") or ""),
            [str(member) for member in members],
            external_id=str(body.get("external_id")
                            or body.get("externalId") or ""))

    def list_groups(self, workspace_id: str) -> list[dict]:
        """Directory adapter for SCIM (the caller is already authenticated)."""
        return self.service.list_groups(workspace_id)

    def get_group(self, workspace_id: str, actor: str,
                  group_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        row = self.service._control_get("security_group", workspace_id,
                                        group_id)
        if row is None:
            from ...errors import NotFoundError

            raise NotFoundError(f"group not found: {group_id!r}")
        return dict(row)

    def set_group_members(self, workspace_id: str, actor: str, group_id: str,
                          members: list[str]) -> dict:
        """SCIM ``PUT``/``PATCH`` member replacement, one audit event."""
        self._ctx.require_action(actor, workspace_id, "security.manage")
        if not isinstance(members, list):
            raise ValidationError("group members must be a list")
        return self.service.update_group(
            workspace_id, actor, group_id,
            members=[str(member) for member in members])

    def update_group(self, workspace_id: str, actor: str, group_id: str,
                     body: dict) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        members = body.get("members")
        if members is not None and not isinstance(members, list):
            raise ValidationError("group members must be a list")
        return self.service.update_group(
            workspace_id, actor, group_id,
            name=body.get("name"),
            members=None if members is None
            else [str(member) for member in members],
            external_id=(str(body["external_id"])
                         if body.get("external_id") is not None
                         else None))

    def delete_group(self, workspace_id: str, actor: str,
                     group_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.delete_group(workspace_id, actor, group_id)

    def relations(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.list_relations(workspace_id)

    def create_relation(self, workspace_id: str, actor: str,
                        body: dict) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.create_relation(
            workspace_id, actor,
            resource_type=str(body.get("resource_type") or ""),
            resource_id=str(body.get("resource_id") or ""),
            relation=str(body.get("relation") or ""),
            subject_type=str(body.get("subject_type") or "user"),
            subject_id=str(body.get("subject_id") or ""),
        )

    def delete_relation(self, workspace_id: str, actor: str,
                        body: dict) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.manage")
        return self.service.delete_relation(
            workspace_id, actor,
            resource_type=str(body.get("resource_type") or ""),
            resource_id=str(body.get("resource_id") or ""),
            relation=str(body.get("relation") or ""),
            subject_type=str(body.get("subject_type") or "user"),
            subject_id=str(body.get("subject_id") or ""),
        )

    # ---- evidence ---------------------------------------------------------

    def decisions(self, workspace_id: str, actor: str,
                  limit: int = 50) -> list[dict]:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.list_decisions(workspace_id, limit=limit)

    def decision(self, workspace_id: str, actor: str,
                 decision_id: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.get_decision(workspace_id, decision_id)

    def roles(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        view = self.service.roles_view(workspace_id)
        # The console needs the caller's own effective roles to explain why a
        # write button is unavailable; this is not a directory of other users.
        view["principal"] = self.service.principal(actor, workspace_id).to_dict()
        view["can_manage"] = _can(self._ctx, actor, workspace_id,
                                  "security.manage")
        view["can_publish"] = _can(self._ctx, actor, workspace_id,
                                   "security.publish")
        return view

    def scopes(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require_action(actor, workspace_id, "security.read")
        return self.service.scopes_view(workspace_id)

    def validate_blueprint(self, workspace_id: str, actor: str,
                           blueprint: dict,
                           known_objects: list[str] | None = None,
                           known_actions: list[str] | None = None,
                           known_fields: dict | None = None) -> dict:
        """Validate the proposed Blueprint ``security`` block (no publish)."""
        self._ctx.require_action(actor, workspace_id, "security.read")
        from .blueprint import validate_blueprint_security

        return validate_blueprint_security(
            blueprint,
            known_objects=known_objects,
            known_actions=known_actions,
            known_fields=known_fields,
        )

    # ---- object-list PEP --------------------------------------------------

    def filter_objects(self, workspace_id: str, actor: str,
                       objects: list[dict], *,
                       action: str = "object.read",
                       resource_type: str = "object",
                       attributes: dict | None = None) -> list[dict]:
        """Apply row scope + field masks to kernel object rows.

        Kernel objects carry their business fields under ``properties``; the
        scope language addresses fields at the row level, so the PEP flattens
        them for evaluation and writes only masked values back into the
        returned copy.  The original rows are never mutated.
        """
        from copy import deepcopy

        from ...domain.security.model import Resource
        from .masking import apply_field_masks

        self._ctx.require_action(actor, workspace_id, "workspace.read")
        principal = self.service.principal(actor, workspace_id,
                                           attributes=attributes)
        tenant = self.service.tenant_context(workspace_id)
        decision = self.service.decision_for(
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
        scope = self.service.authorized_scope(decision, principal, tenant)
        masks: list[dict] = []
        for obligation in decision.obligations:
            if obligation.kind == "field_mask":
                masks.extend(obligation.value.get("fields") or [])
        kept: list[dict] = []
        for obj in objects:
            props = dict(obj.get("properties") or {})
            flat = {**props, **obj, "tenant_id": tenant.tenant_id,
                    "workspace_id": workspace_id}
            if not scope.filter([flat]):
                continue
            masked = apply_field_masks([flat], masks)[0] if masks else flat
            out = deepcopy(obj)
            out_props = dict(out.get("properties") or {})
            for key in list(out_props):
                if key in masked:
                    out_props[key] = masked[key]
            out["properties"] = out_props
            kept.append(out)
        return kept

    def query_objects(self, workspace_id: str, actor: str, kind: str, *,
                      filter: dict | None = None,
                      limit: int | None = 200) -> list[dict]:
        """Authorized object query whose row predicate is pushed to storage.

        Unlike :meth:`filter_objects`, this never loads the whole workspace
        into memory: the PDP scope and the caller's filter are compiled into a
        parameterized SQL predicate when the store supports it.
        """
        self._ctx.require_action(actor, workspace_id, "workspace.read")
        if not str(kind or "").strip():
            raise ValidationError("object kind must not be empty")
        # Business data is readable with workspace.read; control-plane facts
        # (policy, decisions, memberships, MFA/break-glass rows) need the
        # security permission so this generic query cannot become a bypass.
        control_kinds = {
            "workspace", "membership", "tenant", "tenant_membership",
            "llm_config", "security_policy", "role_binding",
            "security_group", "relation_tuple", "authorization_decision",
            "audit", "event", "org_unit", "identity_user", "webhook",
            "scheduler_task", "async_task", "break_glass", "mfa_enrollment",
        }
        if str(kind) in control_kinds:
            self._ctx.require_action(actor, workspace_id, "security.read")
        return self._ctx.repo.query(
            str(kind), workspace_id, filter=filter,
            limit=None if limit is None else max(0, int(limit)))


__all__ = ["SecurityUseCases"]
