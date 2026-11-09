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
``rule.evaluate`` with the object/rule/violation counts.  Evaluation is
page-based (stable object id order), and ``simulate`` applies hypothetical
top-level property values to one object for rule-level what-if analysis.
"""

from __future__ import annotations

import copy

from ..domain.rule.engine import evaluate_aggregate_rule, evaluate_rules
from ..domain.rule.model import (
    RULE_LIFECYCLES,
    SEVERITIES,
    RuleSpec,
    validate_rule_definition,
)
from ..errors import ConcurrencyConflictError, NotFoundError, ValidationError
from .common import ApplicationContext, active_ontology_row
from .rule_index import RuleFieldIndex


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


def _pruned_candidates(candidates: list[dict], rules: list[RuleSpec],
                       field_index: RuleFieldIndex) -> list[dict] | None:
    """Objects that can still match at least one rule, or ``None`` for all.

    ``None`` means "cannot prune safely": an aggregate rule must see every
    parent (cross-object result depends on the full set), and a rule the index
    cannot answer forces a full scan.  Intersecting/unioning only sound
    postings keeps index=True and index=False violation sets identical.
    """
    if not rules or any(rule.kind == "aggregate" for rule in rules):
        return None
    matched: set[str] = set()
    for rule in rules:
        ids = field_index.candidates(rule)
        if ids is None:
            return None
        matched |= ids
    return [obj for obj in candidates
            if str(obj.get("id") or "") in matched]


#: Evaluation is page-based so a large object type cannot build one unbounded
#: response.  The default keeps the historical single-call behaviour for small
#: workspaces while still making the cap explicit.
MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100


def _page_params(limit: object, offset: object) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit must be an integer")
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValidationError(
            f"limit must be between 1 and {MAX_PAGE_SIZE}, got {limit}")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValidationError("offset must be an integer")
    if offset < 0:
        raise ValidationError(f"offset must be >= 0, got {offset}")
    return limit, offset


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
        kind: str = "constraint",
        aggregate: dict | None = None,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        now = self._ctx.clock()
        rule = RuleSpec(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name,
            description=description,
            object_type=object_type,
            kind=kind,
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
            aggregate=copy.deepcopy(aggregate) if aggregate is not None else None,
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
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        index: bool = False,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        limit, offset = _page_params(limit, offset)
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
        candidates: list[dict] = []
        if refs is not None:
            for ref in refs:
                obj = gateway.get_object(ref)
                if obj is None:
                    missing.append(ref)
                    continue
                if object_type is not None and obj.get("type") != object_type:
                    continue
                candidates.append(obj)
        else:
            candidates = [obj for obj in gateway.find_objects(object_type)
                          if isinstance(obj, dict)]
            # Stable page boundaries: gateway order is not a contract.
            candidates.sort(key=lambda obj: str(obj.get("id") or ""))

        total_objects = len(candidates)
        if object_type is not None:
            target_types = {object_type}
        else:
            target_types = {str(obj.get("type")) for obj in candidates
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
        aggregate_rules = [rule for rule in rules
                           if rule.kind == "aggregate"]

        # Optional candidate pruning (P2.5).  The index is built for the one
        # evaluated object type; it is never used for ``refs`` mode and never
        # when an aggregate rule is present (an aggregate must see every
        # parent, so pruning the parent page would change its result).
        index_stats = None
        page_source = candidates
        if index:
            index_stats = {
                "enabled": True,
                "mode": "type" if object_type is not None else "refs",
                "pruned": False,
                "candidates": len(candidates),
                "scanned": 0,
                "fields": 0,
                "values": 0,
                "rules_indexed": 0,
                "rules_scanned": len(rules),
            }
            if object_type is not None:
                field_index = RuleFieldIndex.build(gateway, object_type, rules)
                index_stats.update(field_index.stats())
                pruned = _pruned_candidates(candidates, rules, field_index)
                if pruned is not None:
                    page_source = pruned
                    index_stats["pruned"] = True
                    index_stats["candidates"] = len(pruned)

        page = page_source[offset:offset + limit]
        evaluated_objects = len(page)
        has_more = offset + evaluated_objects < len(page_source)
        next_offset = offset + evaluated_objects if has_more else None

        violations: list[dict] = []
        evaluated_rules = 0
        skipped = 0
        for obj in page:
            applicable = [rule for rule in rules
                          if rule.object_type == obj.get("type")
                          and rule.kind != "aggregate"]
            if applicable:
                result = evaluate_rules(applicable, obj)
                evaluated_rules += result.evaluated_rules
                skipped += result.skipped
                violations.extend(result.violations)
            for rule in aggregate_rules:
                if rule.object_type != obj.get("type"):
                    continue
                spec = rule.aggregate if isinstance(rule.aggregate, dict) else {}
                relation = str(spec.get("relation") or "")
                ref = f"{obj.get('type')}/{obj.get('id')}"
                child_refs: list[str] = []
                if relation:
                    try:
                        child_refs = list(gateway.related(ref, relation) or [])
                    except Exception:  # noqa: BLE001 - missing relation = no children
                        child_refs = []
                children: list[dict] = []
                for child_ref in child_refs:
                    try:
                        child = gateway.get_object(child_ref)
                    except Exception:  # noqa: BLE001 - unreadable child = absent
                        child = None
                    if isinstance(child, dict):
                        children.append(child)
                result = evaluate_aggregate_rule(rule, obj, children)
                if result is None:
                    continue
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
            {"object_count": total_objects, "evaluated_objects": evaluated_objects,
             "rule_count": len(rules), "violations": len(violations),
             "missing": len(missing), "include_drafts": bool(include_drafts),
             "evaluated_rules": evaluated_rules, "skipped": skipped,
             "aggregate_rules": len(aggregate_rules),
             "index": bool(index),
             "indexed_rules": (
                 index_stats["rules_indexed"] if index_stats else 0),
             "offset": offset, "limit": limit, "has_more": has_more})
        result = {
            "passed": passed,
            # ``object_count`` stays the total candidate count for backward
            # compatibility; ``evaluated_objects`` is the current page.
            "object_count": total_objects,
            "total_objects": total_objects,
            "evaluated_objects": evaluated_objects,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": next_offset,
            "rule_count": len(rules),
            "violations": violations,
            "missing": missing,
            "by_severity": by_severity,
            # small, additive diagnostics: disabled/`when`-skipped rules never
            # evaluate but the caller deserves to know they were skipped.
            "evaluated_rules": evaluated_rules,
            "skipped": skipped,
        }
        # Additive only when requested: index=False responses keep exactly the
        # historical key set.
        if index:
            result["index_stats"] = index_stats
        return result


class SimulateRuleChanges:
    """Rule-level what-if: evaluate one object with hypothetical property values.

    This is deliberately *not* a Digital Twin: it applies top-level property
    overrides to a single existing object, re-evaluates the same published
    rules and reports which violations resolved or appeared.  There is no
    event propagation, no relation traversal and no side effect.
    """

    _RESERVED = {"id", "type", "properties", "workspace_id", "tenant_id"}

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    @staticmethod
    def _summary(result) -> dict:
        by_severity = {severity: 0 for severity in SEVERITIES}
        for violation in result.violations:
            severity = violation.get("severity")
            if severity in by_severity:
                by_severity[severity] += 1
        return {
            "passed": result.passed,
            "violations": result.violations,
            "by_severity": by_severity,
            "evaluated_rules": result.evaluated_rules,
            "skipped": result.skipped,
        }

    def __call__(
        self,
        workspace_id: str,
        actor: str,
        ref: str,
        changes: dict,
        include_drafts: bool = False,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        if not isinstance(changes, dict) or not changes:
            raise ValidationError("changes must be a non-empty object")
        for key in changes:
            if not isinstance(key, str) or not key.strip():
                raise ValidationError(
                    "change keys must be non-empty property names")
            name = key.strip()
            if name in self._RESERVED or name.startswith("$") or "." in name:
                raise ValidationError(
                    f"unsupported change field: {key!r}; only top-level "
                    "properties may be simulated")
        gateway = self._ctx.gateways.for_workspace(
            workspace_id, self._ctx.default_runtime_config)
        obj = gateway.get_object(ref)
        if obj is None:
            raise NotFoundError(f"object not found: {ref!r}")
        object_type = str(obj.get("type") or "")
        allowed_lifecycles = {"draft", "published"} if include_drafts else {
            "published"}
        rules = [
            RuleSpec.from_dict(row)
            for row in self._ctx.repo.list("rule", workspace_id)
            if row.get("lifecycle") in allowed_lifecycles
            and row.get("enabled", True)
            and row.get("object_type") == object_type
        ]
        # Simulation is single-object only (P2.5 out of scope): aggregate
        # rules are ignored rather than evaluated with a constraint AST.
        rules = [rule for rule in rules if rule.kind != "aggregate"]
        before = evaluate_rules(rules, obj)
        simulated = copy.deepcopy(obj)
        properties = simulated.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        properties.update(copy.deepcopy(changes))
        simulated["properties"] = properties
        after = evaluate_rules(rules, simulated)

        before_ids = {item["rule_id"] for item in before.violations}
        after_ids = {item["rule_id"] for item in after.violations}
        resolved = sorted(before_ids - after_ids)
        introduced = sorted(after_ids - before_ids)
        self._ctx.audit_event(
            actor, workspace_id, "rule.simulate", ref,
            {"object_type": object_type, "fields": len(changes),
             "resolved": len(resolved), "introduced": len(introduced),
             "passed_before": before.passed, "passed_after": after.passed})
        return {
            "ref": ref,
            "object_type": object_type,
            "changes": copy.deepcopy(changes),
            "before": self._summary(before),
            "after": self._summary(after),
            "delta": {
                "resolved": resolved,
                "introduced": introduced,
                "changed": bool(resolved or introduced),
            },
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
        self.simulate = SimulateRuleChanges(ctx)


__all__ = [
    "CreateRule",
    "DeleteRule",
    "DisableRule",
    "EvaluateRules",
    "ListRules",
    "PublishRule",
    "RuleUseCases",
    "SimulateRuleChanges",
    "UpdateRule",
]
