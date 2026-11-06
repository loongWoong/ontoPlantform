"""P1-Rule: the declarative rule contract and the pure evaluation engine.

The engine is the part of P1 that must be provably safe: rules are JSON, the
operator set is closed, and evaluation never executes user code.  These tests
pin the operator semantics (including the deliberate MISSING behaviour), the
AST validation boundary, ordering, severity handling and determinism.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import onto_platform.domain.rule.engine as engine_module
import onto_platform.domain.rule.model as model_module
from onto_platform.domain.ontology.model import ObjectTypeSpec, PropertySpec
from onto_platform.domain.rule import (
    OPERATORS,
    RuleEvaluation,
    RuleSpec,
    RuleViolation,
    evaluate_condition,
    evaluate_rule,
    evaluate_rules,
    validate_condition,
    validate_rule_definition,
)
from onto_platform.errors import ValidationError


def make_rule(condition: dict, **overrides) -> RuleSpec:
    data = {
        "id": "rule_1",
        "workspace_id": "ws_1",
        "name": "余额约束",
        "description": "",
        "object_type": "Vehicle",
        "kind": "constraint",
        "severity": "error",
        "condition": condition,
        "when": {},
        "message": "违反约束",
        "priority": 100,
        "enabled": True,
        "lifecycle": "draft",
        "version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    data.update(overrides)
    return RuleSpec.from_dict(data)


def make_object(**properties) -> dict:
    return {"type": "Vehicle", "id": "V001", "properties": properties}


BASE_OBJECT = make_object(
    plate="鲁A12345",
    balance=100,
    tags=["a", "b"],
    note="hello world",
    status=None,
    flag=False,
)


# ----------------------------------------------------------------------
# operators
# ----------------------------------------------------------------------

OPERATOR_CASES = [
    ({"field": "plate", "op": "eq", "value": "鲁A12345"}, True),
    ({"field": "plate", "op": "eq", "value": "鲁B00000"}, False),
    ({"field": "status", "op": "eq", "value": None}, True),
    ({"field": "plate", "op": "ne", "value": "鲁B00000"}, True),
    ({"field": "plate", "op": "ne", "value": "鲁A12345"}, False),
    ({"field": "balance", "op": "gt", "value": 50}, True),
    ({"field": "balance", "op": "gt", "value": 200}, False),
    ({"field": "balance", "op": "gte", "value": 100}, True),
    ({"field": "balance", "op": "lt", "value": 200}, True),
    ({"field": "balance", "op": "lte", "value": 99}, False),
    ({"field": "balance", "op": "in", "value": [100, 200]}, True),
    ({"field": "balance", "op": "in", "value": [1, 2]}, False),
    ({"field": "balance", "op": "not_in", "value": [1, 2]}, True),
    ({"field": "balance", "op": "not_in", "value": [100]}, False),
    ({"field": "tags", "op": "contains", "value": "a"}, True),
    ({"field": "tags", "op": "contains", "value": "c"}, False),
    ({"field": "note", "op": "contains", "value": "hello"}, True),
    ({"field": "note", "op": "contains", "value": "zzz"}, False),
    ({"field": "tags", "op": "not_contains", "value": "c"}, True),
    ({"field": "tags", "op": "not_contains", "value": "a"}, False),
    ({"field": "note", "op": "not_contains", "value": "zzz"}, True),
    ({"field": "status", "op": "is_null"}, True),
    ({"field": "plate", "op": "is_null"}, False),
    ({"field": "status", "op": "not_null"}, False),
    ({"field": "plate", "op": "not_null"}, True),
    ({"field": "note", "op": "starts_with", "value": "hello"}, True),
    ({"field": "note", "op": "starts_with", "value": "world"}, False),
    ({"field": "note", "op": "ends_with", "value": "world"}, True),
    ({"field": "note", "op": "ends_with", "value": "hello"}, False),
    ({"field": "$id", "op": "eq", "value": "V001"}, True),
    ({"field": "$type", "op": "eq", "value": "Vehicle"}, True),
]


@pytest.mark.parametrize(("condition", "expected"), OPERATOR_CASES)
def test_operator_semantics(condition, expected):
    assert evaluate_condition(condition, BASE_OBJECT) is expected


def test_every_declared_operator_is_covered_by_tests():
    exercised = {condition["op"] for condition, _ in OPERATOR_CASES}
    assert exercised == set(OPERATORS)


def test_bool_is_not_a_number():
    # True == 1 in Python, but a boolean is not a business number: numeric
    # operators must not silently accept it on either side.
    assert evaluate_condition(
        {"field": "flag", "op": "gt", "value": 0}, BASE_OBJECT) is False
    assert evaluate_condition(
        {"field": "flag", "op": "lt", "value": 2}, BASE_OBJECT) is False
    # comparing a number against a bool literal is rejected outright
    with pytest.raises(ValidationError):
        evaluate_condition(
            {"field": "balance", "op": "gt", "value": True}, BASE_OBJECT)


# ----------------------------------------------------------------------
# logical composition
# ----------------------------------------------------------------------


def test_all_any_not():
    all_true = {"all": [
        {"field": "balance", "op": "gte", "value": 0},
        {"field": "plate", "op": "starts_with", "value": "鲁"},
    ]}
    assert evaluate_condition(all_true, BASE_OBJECT) is True

    assert evaluate_condition(
        {"all": [all_true["all"][0],
                 {"field": "balance", "op": "gt", "value": 1000}]},
        BASE_OBJECT) is False
    assert evaluate_condition(
        {"any": [{"field": "balance", "op": "gt", "value": 1000},
                 {"field": "plate", "op": "eq", "value": "鲁A12345"}]},
        BASE_OBJECT) is True
    assert evaluate_condition(
        {"any": [{"field": "balance", "op": "gt", "value": 1000}]},
        BASE_OBJECT) is False
    assert evaluate_condition(
        {"not": {"field": "balance", "op": "lt", "value": 0}},
        BASE_OBJECT) is True
    assert evaluate_condition(
        {"not": {"any": [{"field": "balance", "op": "gt", "value": 1000}]}},
        BASE_OBJECT) is True
    # deeply nested composition
    nested = {"all": [
        {"any": [{"not": {"field": "plate", "op": "eq", "value": "X"}}]},
        {"all": [{"field": "balance", "op": "lte", "value": 100}]},
    ]}
    assert evaluate_condition(nested, BASE_OBJECT) is True


# ----------------------------------------------------------------------
# missing properties never raise
# ----------------------------------------------------------------------


def test_missing_field_matrix():
    expressions = [
        {"field": "ghost", "op": "eq", "value": 1},
        {"field": "ghost", "op": "ne", "value": 1},
        {"field": "ghost", "op": "gt", "value": 0},
        {"field": "ghost", "op": "gte", "value": 0},
        {"field": "ghost", "op": "lt", "value": 0},
        {"field": "ghost", "op": "lte", "value": 0},
        {"field": "ghost", "op": "in", "value": [1]},
        {"field": "ghost", "op": "not_in", "value": [1]},
        {"field": "ghost", "op": "contains", "value": "x"},
        {"field": "ghost", "op": "not_contains", "value": "x"},
        {"field": "ghost", "op": "starts_with", "value": "x"},
        {"field": "ghost", "op": "ends_with", "value": "x"},
    ]
    for condition in expressions:
        assert evaluate_condition(condition, BASE_OBJECT) is False, condition
    assert evaluate_condition({"field": "ghost", "op": "is_null"},
                              BASE_OBJECT) is True
    assert evaluate_condition({"field": "ghost", "op": "not_null"},
                              BASE_OBJECT) is False


def test_object_without_properties_mapping_is_all_missing():
    sparse = {"type": "Vehicle", "id": "V002"}
    assert evaluate_condition({"field": "balance", "op": "gte", "value": 0},
                              sparse) is False
    assert evaluate_condition({"field": "balance", "op": "is_null"},
                              sparse) is True


# ----------------------------------------------------------------------
# invalid AST raises ValidationError (never silently passes)
# ----------------------------------------------------------------------

INVALID_CONDITIONS = [
    None,
    [],
    "balance gt 0",
    {},
    {"field": "x"},
    {"field": "x", "op": "regex", "value": ".*"},
    {"field": "x", "op": "in", "value": "not-a-list"},
    {"field": "x", "op": "gt", "value": "5"},
    {"field": "x", "op": "gte", "value": True},
    {"field": "x", "op": "contains", "value": {"a": 1}},
    {"field": "x", "op": "eq", "value": [1, 2]},
    {"field": "x", "op": "is_null", "value": 1},
    {"field": "", "op": "eq", "value": 1},
    {"field": 5, "op": "eq", "value": 1},
    {"op": "eq", "value": 1},
    {"field": "x", "op": "eq", "value": 1, "extra": 2},
    {"all": "not-a-list"},
    {"all": []},
    {"any": []},
    {"not": "not-an-object"},
    {"all": [], "any": []},
    {"all": [{"field": "x", "op": "eq", "value": 1}], "field": "y"},
]


@pytest.mark.parametrize("condition", INVALID_CONDITIONS)
def test_invalid_ast_raises_validation_error(condition):
    with pytest.raises(ValidationError):
        evaluate_condition(condition, BASE_OBJECT)


def test_deeply_nested_ast_is_rejected_not_crashing():
    condition: dict = {"field": "x", "op": "eq", "value": 1}
    for _ in range(40):
        condition = {"not": condition}
    problems = validate_condition(condition)
    assert problems
    assert any("deeper than" in problem for problem in problems)
    with pytest.raises(ValidationError):
        evaluate_condition(condition, BASE_OBJECT)


# ----------------------------------------------------------------------
# RuleSpec round-trip and validation
# ----------------------------------------------------------------------


def test_rule_spec_round_trip_and_unknown_fields_ignored():
    rule = make_rule(
        {"field": "balance", "op": "lt", "value": 0},
        description="余额不得为负",
        severity="warning",
        priority=5,
        when={"field": "balance", "op": "not_null"},
    )
    doc = rule.to_dict()
    assert set(doc) == {
        "id", "workspace_id", "name", "description", "object_type", "kind",
        "severity", "condition", "when", "message", "priority", "enabled",
        "lifecycle", "version", "created_at", "updated_at",
    }
    loaded = RuleSpec.from_dict({**doc, "future_field": "ignored"})
    assert loaded.to_dict() == doc
    # storage must not alias the in-memory AST
    doc["condition"]["field"] = "mutated"
    assert rule.condition["field"] == "balance"
    assert loaded.condition["field"] == "balance"


def test_rule_violation_and_evaluation_to_dict():
    violation = RuleViolation(
        rule_id="r1", rule_name="余额", object_type="Vehicle",
        object_id="V001", severity="error", message="余额不能为负",
        field="balance")
    assert violation.to_dict() == {
        "rule_id": "r1",
        "rule_name": "余额",
        "object_type": "Vehicle",
        "object_id": "V001",
        "severity": "error",
        "message": "余额不能为负",
        "field": "balance",
        "detail": {},
    }
    evaluation = RuleEvaluation(passed=False, evaluated_rules=2,
                                violations=[violation.to_dict()], skipped=1)
    assert evaluation.to_dict()["passed"] is False
    assert evaluation.to_dict()["violations"][0]["rule_id"] == "r1"


VALID_SPEC = {"name": "Vehicle", "properties": {
    "plate": {"name": "plate"},
    "balance": {"name": "balance"},
}}


def test_validate_rule_definition_accepts_a_valid_rule():
    rule = make_rule({"field": "balance", "op": "gte", "value": 0},
                     when={"field": "plate", "op": "not_null"})
    assert validate_rule_definition(rule, VALID_SPEC) == []
    # $id / $type are meta-fields, not properties of the object type
    meta = make_rule({"field": "$id", "op": "not_null"})
    assert validate_rule_definition(meta, VALID_SPEC) == []
    # the dataclass form of ObjectTypeSpec is accepted too
    spec = ObjectTypeSpec(
        name="Vehicle", description="车辆",
        properties={
            "plate": PropertySpec(name="plate", type="string"),
            "balance": PropertySpec(name="balance", type="number"),
        })
    assert validate_rule_definition(rule, spec) == []


@pytest.mark.parametrize(("overrides", "fragment"), [
    ({"object_type": "Ghost"}, "not defined in the ontology"),
    ({"condition": {"field": "ghost", "op": "eq", "value": 1}},
     "is not a property defined in the current ontology"),
    ({"name": "   "}, "name must not be empty"),
    ({"message": ""}, "message must not be empty"),
    ({"severity": "fatal"}, "severity"),
    ({"kind": "association"}, "kind"),
    ({"priority": True}, "priority must be an int"),
    ({"priority": "100"}, "priority must be an int"),
    ({"lifecycle": "deleted"}, "lifecycle"),
    ({"version": 0}, "version must be a positive int"),
    ({"enabled": "yes"}, "enabled must be a boolean"),
    ({"condition": {"field": "balance", "op": "regex", "value": "x"}},
     "not supported"),
    ({"when": {"field": "ghost", "op": "eq", "value": 1}},
     "is not a property defined in the current ontology"),
    ({"when": "nonsense"}, "when must be an object"),
])
def test_validate_rule_definition_reports_problems(overrides, fragment):
    params = {"condition": {"field": "balance", "op": "gte", "value": 0}}
    params.update(overrides)
    rule = make_rule(**params)
    problems = validate_rule_definition(rule, VALID_SPEC)
    assert problems
    assert any(fragment in problem for problem in problems), problems


def test_validate_rule_definition_reports_missing_object_type():
    rule = make_rule({"field": "balance", "op": "gte", "value": 0})
    problems = validate_rule_definition(rule, None)
    assert any("not defined in the ontology" in problem for problem in problems)


def test_validate_rule_definition_checks_field_against_object_spec_only_when_known():
    rule = make_rule({"field": "anything", "op": "eq", "value": 1})
    # structural validation without an object spec passes the field check
    assert validate_condition(rule.condition) == []
    # ... but a real spec reports the unknown field
    assert validate_condition(rule.condition, properties={"plate"}) != []


# ----------------------------------------------------------------------
# evaluation ordering, severity, when, enabled
# ----------------------------------------------------------------------


def test_rules_are_ordered_by_priority_then_name_then_id():
    condition = {"field": "balance", "op": "lt", "value": 1000}
    rules = [
        make_rule(condition, id="rule_b", name="B", priority=20),
        make_rule(condition, id="rule_a", name="A", priority=10),
        make_rule(condition, id="rule_d", name="C", priority=20),
        make_rule(condition, id="rule_c", name="C", priority=20),
    ]
    result = evaluate_rules(rules, BASE_OBJECT)
    assert [item["rule_id"] for item in result.violations] == [
        "rule_a", "rule_b", "rule_c", "rule_d"]
    assert result.evaluated_rules == 4
    assert result.skipped == 0
    assert result.passed is False


def test_warning_and_info_do_not_fail_evaluation():
    condition = {"field": "balance", "op": "lt", "value": 1000}
    warning = evaluate_rules(
        [make_rule(condition, severity="warning")], BASE_OBJECT)
    assert warning.passed is True
    assert len(warning.violations) == 1
    assert warning.violations[0]["severity"] == "warning"

    info = evaluate_rules([make_rule(condition, severity="info")], BASE_OBJECT)
    assert info.passed is True
    assert info.violations[0]["severity"] == "info"

    mixed = evaluate_rules(
        [make_rule(condition, severity="warning", name="A_warning"),
         make_rule(condition, severity="error", id="rule_2", name="Z_error")],
        BASE_OBJECT)
    assert mixed.passed is False
    assert [item["severity"] for item in mixed.violations] == [
        "warning", "error"]


def test_when_precondition_skips_rule():
    rule = make_rule(
        {"field": "balance", "op": "lt", "value": 0},
        when={"field": "balance", "op": "gt", "value": 50})
    # precondition false -> skipped, never evaluated, never a violation
    low = evaluate_rule(rule, make_object(balance=20))
    assert low.passed is True
    assert low.skipped == 1
    assert low.evaluated_rules == 0
    assert low.violations == []
    # precondition true -> evaluated (condition false -> pass)
    high = evaluate_rule(rule, make_object(balance=100))
    assert high.skipped == 0
    assert high.evaluated_rules == 1
    assert high.passed is True
    # precondition true and condition true -> violation
    fired = evaluate_rule(rule, make_object(balance=100))
    assert fired.passed is True  # condition is false for balance=100
    firing_rule = make_rule(
        {"field": "balance", "op": "lt", "value": 200},
        when={"field": "balance", "op": "gt", "value": 50})
    assert evaluate_rule(firing_rule, make_object(balance=100)).passed is False


def test_disabled_rule_is_skipped_even_if_condition_fires():
    rule = make_rule({"field": "balance", "op": "lt", "value": 1000},
                     enabled=False)
    single = evaluate_rule(rule, BASE_OBJECT)
    assert single.passed is True
    assert single.skipped == 1
    assert single.violations == []
    aggregate = evaluate_rules([rule], BASE_OBJECT)
    assert aggregate.skipped == 1
    assert aggregate.evaluated_rules == 0
    assert aggregate.violations == []


def test_violation_payload_includes_object_and_rule_identity():
    rule = make_rule(
        {"field": "balance", "op": "lt", "value": 0},
        id="rule_neg", name="余额不能为负", message="余额不能为负")
    result = evaluate_rule(rule, make_object(balance=-5))
    assert result.passed is False
    assert result.evaluated_rules == 1
    violation = result.violations[0]
    assert violation["rule_id"] == "rule_neg"
    assert violation["rule_name"] == "余额不能为负"
    assert violation["object_type"] == "Vehicle"
    assert violation["object_id"] == "V001"
    assert violation["severity"] == "error"
    assert violation["message"] == "余额不能为负"
    assert violation["field"] == "balance"
    assert violation["detail"]["op"] == "lt"
    assert violation["detail"]["expected"] == 0
    assert violation["detail"]["actual"] == -5


def test_evaluate_rules_is_deterministic_and_does_not_mutate_rules():
    condition = {"all": [
        {"field": "balance", "op": "gte", "value": 0},
        {"field": "plate", "op": "not_null"},
    ]}
    rules = [make_rule(condition, severity="warning"),
             make_rule(condition, id="rule_2", name="Z")]
    before = [rule.to_dict() for rule in rules]
    first = evaluate_rules(rules, BASE_OBJECT).to_dict()
    second = evaluate_rules(rules, BASE_OBJECT).to_dict()
    assert first == second
    assert [rule.to_dict() for rule in rules] == before


# ----------------------------------------------------------------------
# architectural guard: no code execution, no upward imports
# ----------------------------------------------------------------------


def test_engine_never_executes_user_code_and_stays_in_domain():
    for module in (engine_module, model_module):
        source = inspect.getsource(module)
        assert "eval(" not in source
        assert "exec(" not in source
        assert "__import__" not in source
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for name in imported:
            assert "application" not in name, name
            assert "infrastructure" not in name, name
