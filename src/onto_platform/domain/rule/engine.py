"""Pure evaluation of declared rules (P1-Rule).

The engine only walks a closed JSON AST built by :mod:`model`; it never
executes user code (no ``eval``/``exec``) and has no application or
infrastructure imports.  Standard library only.

Missing properties follow the semantics documented in :mod:`model`: a missing
property is ``MISSING``; ``is_null``/``not_null`` treat it as null and every
other operator returns False instead of raising.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable

from ...errors import ValidationError
from .model import (
    MISSING,
    RuleEvaluation,
    RuleSpec,
    RuleViolation,
    validate_condition,
)

_NUMERIC_OPERATORS = frozenset({"gt", "gte", "lt", "lte"})


def _require_object(obj: object) -> None:
    if not isinstance(obj, dict):
        raise ValidationError(
            "rule evaluation needs an object mapping with 'type', 'id' and "
            "'properties'")


def _field_value(obj: dict, field_name: str) -> object:
    if field_name == "$id":
        return obj.get("id", MISSING)
    if field_name == "$type":
        return obj.get("type", MISSING)
    properties = obj.get("properties")
    if not isinstance(properties, dict):
        return MISSING
    return properties.get(field_name, MISSING)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _contains(actual: object, expected: object) -> bool:
    if isinstance(actual, str):
        return isinstance(expected, str) and expected in actual
    if isinstance(actual, (list, tuple, set, frozenset)):
        try:
            return expected in actual
        except TypeError:  # unhashable expected against a set
            return False
    return False


def _is_container(actual: object) -> bool:
    return isinstance(actual, (str, list, tuple, set, frozenset))


def _compare_numeric(op: str, actual: object, expected: object) -> bool:
    if not (_is_number(actual) and _is_number(expected)):
        return False
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "lt":
        return actual < expected
    return actual <= expected


def _evaluate_node(node: dict, obj: dict) -> bool:
    """Evaluate one (already validated) AST node."""
    if "all" in node:
        return all(_evaluate_node(item, obj) for item in node["all"])
    if "any" in node:
        return any(_evaluate_node(item, obj) for item in node["any"])
    if "not" in node:
        return not _evaluate_node(node["not"], obj)

    actual = _field_value(obj, node["field"])
    op = node["op"]

    if op == "is_null":
        return actual is MISSING or actual is None
    if op == "not_null":
        return actual is not MISSING and actual is not None
    if actual is MISSING:
        # "unknown" never becomes equality/inequality/order: the rule simply
        # does not fire for an object that does not carry the property.
        return False

    expected = node.get("value")
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op in _NUMERIC_OPERATORS:
        return _compare_numeric(op, actual, expected)
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op == "contains":
        return _contains(actual, expected)
    if op == "not_contains":
        return _is_container(actual) and not _contains(actual, expected)
    if op == "starts_with":
        return (isinstance(actual, str) and isinstance(expected, str)
                and actual.startswith(expected))
    if op == "ends_with":
        return (isinstance(actual, str) and isinstance(expected, str)
                and actual.endswith(expected))
    # Unreachable for a validated AST; keep evaluation total instead of
    # silently reporting a pass.
    raise ValidationError(f"unsupported rule operator {op!r}")


def _raise_for_conditions(rule: RuleSpec) -> None:
    problems = validate_condition(rule.condition, where="condition")
    if rule.when:
        problems.extend(validate_condition(rule.when, where="when"))
    if problems:
        raise ValidationError(
            "invalid rule condition: " + "; ".join(problems))


def ensure_valid_condition(condition: object) -> None:
    """Raise :class:`ValidationError` unless ``condition`` is a legal AST."""
    problems = validate_condition(condition, where="condition")
    if problems:
        raise ValidationError(
            "invalid rule condition: " + "; ".join(problems))


def _first_leaf(node: dict) -> dict | None:
    """First leaf in document order (used to describe a violation)."""
    if "all" in node:
        for item in node["all"]:
            leaf = _first_leaf(item)
            if leaf is not None:
                return leaf
        return None
    if "any" in node:
        for item in node["any"]:
            leaf = _first_leaf(item)
            if leaf is not None:
                return leaf
        return None
    if "not" in node:
        return _first_leaf(node["not"])
    return node


def _rule_sort_key(rule: RuleSpec) -> tuple[int, str, str]:
    priority = (rule.priority
                if isinstance(rule.priority, int)
                and not isinstance(rule.priority, bool) else 100)
    name = rule.name if isinstance(rule.name, str) else ""
    rule_id = rule.id if isinstance(rule.id, str) else ""
    return (priority, name, rule_id)


def _build_violation(rule: RuleSpec, obj: dict) -> RuleViolation:
    leaf = _first_leaf(rule.condition) or {}
    field_name = leaf.get("field")
    field_name = field_name.strip() if isinstance(field_name, str) else ""
    detail: dict = {
        "priority": rule.priority,
        "kind": rule.kind,
        "condition": copy.deepcopy(rule.condition),
    }
    if field_name:
        actual = _field_value(obj, field_name)
        detail.update({
            "op": leaf.get("op"),
            "expected": copy.deepcopy(leaf.get("value")),
            "actual": None if actual is MISSING else copy.deepcopy(actual),
        })
    object_type = (rule.object_type
                   if isinstance(rule.object_type, str) and rule.object_type
                   else str(obj.get("type") or ""))
    return RuleViolation(
        rule_id=rule.id,
        rule_name=rule.name,
        object_type=object_type,
        object_id=str(obj.get("id") or ""),
        severity=rule.severity,
        message=rule.message,
        field=field_name,
        detail=detail,
    )


def _evaluate_rule_core(rule: RuleSpec, obj: dict) -> RuleEvaluation:
    if not rule.enabled:
        return RuleEvaluation(passed=True, evaluated_rules=0, violations=[],
                              skipped=1)
    if rule.when and not _evaluate_node(rule.when, obj):
        return RuleEvaluation(passed=True, evaluated_rules=0, violations=[],
                              skipped=1)
    if not _evaluate_node(rule.condition, obj):
        return RuleEvaluation(passed=True, evaluated_rules=1, violations=[],
                              skipped=0)
    violation = _build_violation(rule, obj)
    return RuleEvaluation(
        passed=rule.severity != "error",
        evaluated_rules=1,
        violations=[violation.to_dict()],
        skipped=0,
    )


def evaluate_condition(condition: dict, obj: dict) -> bool:
    """True when ``condition`` holds for ``obj``; invalid AST raises."""
    _require_object(obj)
    ensure_valid_condition(condition)
    return _evaluate_node(condition, obj)


def evaluate_rule(rule: RuleSpec, obj: dict) -> RuleEvaluation:
    """Evaluate one rule against one object.

    A disabled rule or an unsatisfied ``when`` precondition yields
    ``skipped=1`` and ``passed=True``; a firing rule with ``severity="error"``
    makes ``passed=False`` while warning/info severity still reports the
    violation.
    """
    _require_object(obj)
    _raise_for_conditions(rule)
    return _evaluate_rule_core(rule, obj)


def evaluate_rules(rules: Iterable[RuleSpec], obj: dict) -> RuleEvaluation:
    """Evaluate rules in ``(priority, name, id)`` order against one object.

    ``passed`` is True unless at least one ``error`` violation fires;
    warning/info violations are still returned.  ``skipped`` counts disabled
    rules and unsatisfied ``when`` preconditions.
    """
    _require_object(obj)
    ordered = sorted(rules, key=_rule_sort_key)
    # Validate the AST once per rule, not once per object: a large object
    # batch must not pay the recursion price repeatedly.
    for rule in ordered:
        _raise_for_conditions(rule)
    evaluated = 0
    skipped = 0
    violations: list[dict] = []
    for rule in ordered:
        single = _evaluate_rule_core(rule, obj)
        evaluated += single.evaluated_rules
        skipped += single.skipped
        violations.extend(single.violations)
    passed = not any(item.get("severity") == "error" for item in violations)
    return RuleEvaluation(passed=passed, evaluated_rules=evaluated,
                          violations=violations, skipped=skipped)


__all__ = [
    "ensure_valid_condition",
    "evaluate_condition",
    "evaluate_rule",
    "evaluate_rules",
]
