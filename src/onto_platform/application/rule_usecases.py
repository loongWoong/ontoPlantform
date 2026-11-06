"""Rule use cases: declare, edit, publish, list and evaluate business rules.

Rules are design-time documents (repository kind ``rule``) validated against
the ontology revision an editor is working on (``active_ontology_row``).
Evaluation reads semantic objects through the workspace runtime gateway, so a
rule sees exactly the same state as a capability or the console would.

Lifecycle: ``draft`` -> ``published`` -> ``archived``.  Only drafts are
editable; publishing is idempotent; deleting a draft removes it, deleting a
published rule archives it (the audit trail and definition survive).

Every write is audited (``rule.create`` / ``rule.update`` / ``rule.publish``
/ ``rule.disable`` / ``rule.delete``); evaluation is audited as
``rule.evaluate`` with the object/rule/violation counts.
"""

from __future__ import annotations

from ..domain.rule.engine import evaluate_rules
from ..domain.rule.model import (
    RULE_LIFECYCLES,
    SEVERITIES,
    RuleSpec,
    validate_rule_definition,
)
from ..errors import ConcurrencyConflictError, ValidationError
from .common import ApplicationContext, active_ontology_row


def _active_row(ctx: ApplicationContext, workspace_id: str) -> dict:
    row = active_ontology_row(ctx, workspace_id)
    if row is None:
        raise ValidationError(
            "workspace has no ontology revision; create an ontology before "
            "defining rules")
    return row


def _object_spec(row: dict, object_type: object) -> dict | None:
    objects = row.get("objects") or {}
    if not isinstance(objects, dict) or not isinstance(object_type, str):
        return None
    return objects.get(object_type)


def _validate(ctx: ApplicationContext, workspace_id: str,
              rule: RuleSpec) -> None:
    row = _active_row(ctx, workspace_id)
    problems = validate_rule_definition(rule, _object_spec(row, rule.object_type))
    if problems:
        raise ValidationError("rule validation failed: " + "; ".join(problems))


def _split_ref(ref: object) -> tuple[str, str]:
    if not isinstance(ref, str) or "/" not in ref:
        raise ValidationError(
            f"object reference must be 'Type/Id', got {ref!r}")
    object_type, _, object_id = ref.partition("/")
    if not object_type or not object_id:
        raise ValidationError(
            f"object reference must be 'Type/Id', got {ref!r}")
    return object_type, object_id


def _rule_sort_key(rule: dict) -> tuple[int, str, str]:
    priority = rule.get("priority", 100)
    if not isinstance(priority, int) or isinstance(priority, bool):
        priority = 100
    return (priority, str(rule.get("name") or ""), str(rule.get("id") or ""))


class CreateRule:
    """Create a draft rule on an object type of the active ontology."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(
        self,
        workspace_id: str,
        actor: str,
        name: str,
        object_type: str,
        condition: dict,
        description: str = "",
        severity: str = "error",
        when: dict | None = None,
        message: str = "",
        priority: int = 100,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        now = self._ctx.clock()
        rule = RuleSpec(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name,
            description=description,
            object_type=object_type,
            kind="constraint",
            severity=severity,
            condition=condition,
            when=dict(when or {}),
            message=message,
            priority=priority,
            enabled=True,
            lifecycle="draft",
            version=1,
            created_at=now,
            updated_at=now,
        )
        _validate(self._ctx, workspace_id, rule)
        self._ctx.save("rule", rule.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "rule.create", rule.id,
            {"object_type": rule.object_type, "severity": rule.severity,
             "kind": rule.kind, "priority": rule.priority})
        return rule.to_dict()


class UpdateRule:
    """Edit a draft rule in place (published rules are immutable)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(
        self,
        workspace_id: str,
        actor: str,
        rule_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        object_type: str | None = None,
        condition: dict | None = None,
        when: dict | None = None,
        message: str | None = None,
        severity: str | None = None,
        priority: int | None = None,
        expected_version: int | None = None,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data, version = self._ctx.load_versioned("rule", workspace_id, rule_id)
        if data.get("lifecycle") != "draft":
            raise ValidationError(
                f"only a draft rule can be edited; {rule_id!r} is "
                f"{data.get('lifecycle')!r}")
        if expected_version is not None and expected_version != version:
            raise ConcurrencyConflictError(
                f"rule {rule_id!r} changed concurrently: expected version "
                f"{expected_version}, current {version}")

        changes = {
            "name": name,
            "description": description,
            "object_type": object_type,
            "condition": condition,
            "when": when,
            "message": message,
            "severity": severity,
            "priority": priority,
        }
        provided = [key for key, value in changes.items() if value is not None]
        if not provided:
            raise ValidationError(
                "nothing to update: provide at least one rule field")

        rule = RuleSpec.from_dict(data)
        for key in provided:
            setattr(rule, key, changes[key])
        try:
            rule.version = int(rule.version) + 1
        except (TypeError, ValueError):
            rule.version = 2
        rule.updated_at = self._ctx.clock()
        _validate(self._ctx, workspace_id, rule)

        updated = rule.to_dict()
        self._ctx.save_versioned("rule", updated, version)
        self._ctx.audit_event(
            actor, workspace_id, "rule.update", rule.id,
            {"fields": sorted(provided), "version": rule.version})
        return updated


class PublishRule:
    """Publish a draft; publishing an already published rule is a no-op."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, rule_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data, version = self._ctx.load_versioned("rule", workspace_id, rule_id)
        lifecycle = data.get("lifecycle")
        if lifecycle == "published":
            # idempotent: the serving contract already contains this rule
            return RuleSpec.from_dict(data).to_dict()
        if lifecycle != "draft":
            raise ValidationError(
                f"only a draft rule can be published; {rule_id!r} is "
                f"{lifecycle!r}")
        rule = RuleSpec.from_dict(data)
        _validate(self._ctx, workspace_id, rule)
        rule.lifecycle = "published"
        rule.version = int(rule.version) + 1
        rule.updated_at = self._ctx.clock()
        self._ctx.save_versioned("rule", rule.to_dict(), version)
        self._ctx.audit_event(
            actor, workspace_id, "rule.publish", rule.id,
            {"object_type": rule.object_type, "version": rule.version})
        return rule.to_dict()


class DisableRule:
    """Turn a rule off without deleting its definition or audit history."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, rule_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data, version = self._ctx.load_versioned("rule", workspace_id, rule_id)
        rule = RuleSpec.from_dict(data)
        if not rule.enabled:
            return rule.to_dict()  # idempotent
        rule.enabled = False
        rule.updated_at = self._ctx.clock()
        self._ctx.save_versioned("rule", rule.to_dict(), version)
        self._ctx.audit_event(
            actor, workspace_id, "rule.disable", rule.id,
            {"lifecycle": rule.lifecycle, "version": rule.version})
        return rule.to_dict()


class DeleteRule:
    """Delete a draft; archive a published rule instead of losing history."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, rule_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data, version = self._ctx.load_versioned("rule", workspace_id, rule_id)
        lifecycle = data.get("lifecycle")
        if lifecycle == "archived":
            return {"deleted": False, "archived": True, "id": rule_id,
                    "rule": RuleSpec.from_dict(data).to_dict()}
        if lifecycle == "draft":
            self._ctx.repo.delete("rule", workspace_id, rule_id)
            self._ctx.audit_event(
                actor, workspace_id, "rule.delete", rule_id, {"draft": True})
            return {"deleted": True, "archived": False, "id": rule_id}
        rule = RuleSpec.from_dict(data)
        rule.lifecycle = "archived"
        rule.enabled = False
        rule.updated_at = self._ctx.clock()
        self._ctx.save_versioned("rule", rule.to_dict(), version)
        self._ctx.audit_event(
            actor, workspace_id, "rule.delete", rule_id,
            {"archived": True, "lifecycle": "archived",
             "version": rule.version})
        return {"deleted": False, "archived": True, "id": rule_id,
                "rule": rule.to_dict()}


class ListRules:
    """List rules of a workspace, deterministically ordered by rule priority."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 lifecycle: str | None = None) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        if lifecycle is not None and lifecycle not in RULE_LIFECYCLES:
            raise ValidationError(
                f"unknown rule lifecycle {lifecycle!r}; expected one of "
                f"{list(RULE_LIFECYCLES)}")
        rules = [RuleSpec.from_dict(row).to_dict()
                 for row in self._ctx.repo.list("rule", workspace_id)]
        if lifecycle is not None:
            rules = [rule for rule in rules if rule["lifecycle"] == lifecycle]
        rules.sort(key=_rule_sort_key)
        return rules


class EvaluateRules:
    """Evaluate published rules against objects (type mode or ref list)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(
        self,
        workspace_id: str,
        actor: str,
        *,
        object_type: str | None = None,
        refs: list[str] | tuple[str, ...] | None = None,
        include_drafts: bool = False,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        if object_type is None and refs is None:
            raise ValidationError(
                "evaluation needs either object_type or a list of refs")
        if refs is not None:
            if isinstance(refs, str):
                raise ValidationError(
                    "refs must be a list of 'Type/Id' references, not a string")
            refs = list(refs)
            if not refs:
                raise ValidationError("refs must not be empty")
            for ref in refs:
                _split_ref(ref)

        row = _active_row(self._ctx, workspace_id)
        objects_in_ontology = row.get("objects") or {}
        if object_type is not None:
            object_type = str(object_type)
            if object_type not in objects_in_ontology:
                raise ValidationError(
                    f"object type {object_type!r} is not defined in the "
                    "ontology")

        gateway = self._ctx.gateways.for_workspace(
            workspace_id, self._ctx.default_runtime_config)
        missing: list[str] = []
        objects: list[dict] = []
        if refs is not None:
            for ref in refs:
                obj = gateway.get_object(ref)
                if obj is None:
                    missing.append(ref)
                    continue
                if object_type is not None and obj.get("type") != object_type:
                    continue
                objects.append(obj)
        else:
            objects = [obj for obj in gateway.find_objects(object_type)
                       if isinstance(obj, dict)]

        if object_type is not None:
            target_types = {object_type}
        else:
            target_types = {str(obj.get("type")) for obj in objects
                            if obj.get("type")}
        allowed_lifecycles = {"draft", "published"} if include_drafts else {
            "published"}
        rules = [
            RuleSpec.from_dict(row_)
            for row_ in self._ctx.repo.list("rule", workspace_id)
            if row_.get("lifecycle") in allowed_lifecycles
            and row_.get("enabled", True)
            and row_.get("object_type") in target_types
        ]

        violations: list[dict] = []
        evaluated_rules = 0
        skipped = 0
        for obj in objects:
            applicable = [rule for rule in rules
                          if rule.object_type == obj.get("type")]
            if not applicable:
                continue
            result = evaluate_rules(applicable, obj)
            evaluated_rules += result.evaluated_rules
            skipped += result.skipped
            violations.extend(result.violations)

        by_severity = {severity: 0 for severity in SEVERITIES}
        for violation in violations:
            severity = violation.get("severity")
            if severity in by_severity:
                by_severity[severity] += 1
        passed = not any(
            violation.get("severity") == "error" for violation in violations)

        self._ctx.audit_event(
            actor, workspace_id, "rule.evaluate",
            object_type if object_type is not None else "refs",
            {"object_count": len(objects), "rule_count": len(rules),
             "violations": len(violations), "missing": len(missing),
             "include_drafts": bool(include_drafts),
             "evaluated_rules": evaluated_rules, "skipped": skipped})
        return {
            "passed": passed,
            "object_count": len(objects),
            "rule_count": len(rules),
            "violations": violations,
            "missing": missing,
            "by_severity": by_severity,
            # small, additive diagnostics: disabled/`when`-skipped rules never
            # evaluate but the caller deserves to know they were skipped.
            "evaluated_rules": evaluated_rules,
            "skipped": skipped,
        }


class RuleUseCases:
    """Composition-root friendly facade (mirrors other ``*UseCases`` groups)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self.create = CreateRule(ctx)
        self.update = UpdateRule(ctx)
        self.publish = PublishRule(ctx)
        self.disable = DisableRule(ctx)
        self.delete = DeleteRule(ctx)
        self.list = ListRules(ctx)
        self.evaluate = EvaluateRules(ctx)


__all__ = [
    "CreateRule",
    "DeleteRule",
    "DisableRule",
    "EvaluateRules",
    "ListRules",
    "PublishRule",
    "RuleUseCases",
    "UpdateRule",
]
