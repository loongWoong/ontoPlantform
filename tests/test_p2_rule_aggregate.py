"""P2.5: cross-object / aggregate rule contract and evaluation.

An aggregate rule reduces the children reached through one relation
(``count``/``sum``/``avg``/``min``/``max``/``any``/``all``), optionally after a
``where`` predicate, and compares the result with one of the 14 operators.
The definition is a closed dict; the engine stays pure (children are resolved
by the use-case layer).
"""

from __future__ import annotations

import pytest

from onto_platform.domain.rule import RuleSpec, validate_rule_definition
from onto_platform.domain.rule.engine import evaluate_aggregate_rule
from onto_platform.domain.rule.model import (
    METRICS,
    RULE_KINDS,
    validate_aggregate,
)
from onto_platform.errors import ValidationError

PARENT = {"type": "Vehicle", "id": "V001", "properties": {"plate": "鲁A"}}
CHILDREN = [
    {"type": "TollStation", "id": "ST001",
     "properties": {"status": "congested", "value": 10}},
    {"type": "TollStation", "id": "ST002",
     "properties": {"status": "free", "value": 5}},
    {"type": "TollStation", "id": "ST003",
     "properties": {"status": "free", "value": -3}},
]
VALID_SPEC = {"name": "Vehicle", "properties": {
    "plate": {"name": "plate"},
    "balance": {"name": "balance"},
}}


def aggregate(**overrides) -> dict:
    raw = {"relation": "passes", "object_type": "TollStation",
           "metric": "count", "op": "gte", "value": 2}
    raw.update(overrides)
    return raw


def make_rule(definition: dict | None = None, **overrides) -> RuleSpec:
    data = {
        "id": "rule_agg",
        "workspace_id": "ws",
        "name": "聚合约束",
        "object_type": "Vehicle",
        "kind": "aggregate",
        "severity": "error",
        "condition": {},
        "when": {},
        "message": "聚合约束失败",
        "priority": 100,
        "enabled": True,
        "lifecycle": "draft",
        "version": 1,
        "aggregate": aggregate() if definition is None else definition,
    }
    data.update(overrides)
    return RuleSpec.from_dict(data)


def check(rule: RuleSpec, children=CHILDREN, **kwargs):
    return evaluate_aggregate_rule(rule, PARENT, children, **kwargs)


# ----------------------------------------------------------------------
# model / validation
# ----------------------------------------------------------------------


def test_old_constraint_rows_stay_byte_compatible():
    old = {
        "id": "r1", "workspace_id": "ws", "name": "余额", "description": "",
        "object_type": "Vehicle", "kind": "constraint", "severity": "error",
        "condition": {"field": "balance", "op": "lt", "value": 0},
        "when": {}, "message": "余额不能为负", "priority": 100,
        "enabled": True, "lifecycle": "draft", "version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    loaded = RuleSpec.from_dict(old)

    assert loaded.kind == "constraint"
    assert loaded.aggregate is None
    assert loaded.to_dict() == old
    assert "aggregate" not in loaded.to_dict()


def test_aggregate_row_round_trips():
    rule = make_rule(aggregate(metric="sum", field="value", op="gte",
                               value=10))

    loaded = RuleSpec.from_dict(rule.to_dict())
    assert loaded.aggregate == rule.aggregate
    assert loaded.kind == "aggregate"


def test_kind_and_metric_sets_are_closed():
    assert RULE_KINDS == ("constraint", "aggregate")
    assert "count" in METRICS and "sum" in METRICS and "any" in METRICS


def test_valid_aggregate_definition_passes():
    rule = make_rule(aggregate(
        where={"field": "status", "op": "eq", "value": "congested"},
        metric="count", op="gte", value=1))

    assert validate_aggregate(rule.aggregate) == []
    assert validate_rule_definition(rule, VALID_SPEC) == []


def test_aggregate_condition_is_ignored():
    rule = make_rule({"all": []})  # would be an invalid constraint AST
    rule.aggregate = aggregate()
    assert validate_rule_definition(rule, VALID_SPEC) == []


def test_non_aggregate_kinds_still_validate_the_condition():
    rule = make_rule({"all": []}, kind="constraint")
    problems = validate_rule_definition(rule, VALID_SPEC)
    assert any("condition" in problem for problem in problems)


@pytest.mark.parametrize(("definition", "fragment"), [
    (None, "aggregate must be an object"),
    ("nonsense", "aggregate must be an object"),
    ({}, "aggregate must not be empty"),
    ({"relation": "passes", "object_type": "TollStation", "metric": "count",
      "op": "gte", "value": 0, "ghost": 1}, "unknown fields"),
    (aggregate(relation=" "), "relation must be a non-empty string"),
    (aggregate(object_type=3), "object_type must be a non-empty string"),
    (aggregate(metric="median"), "metric"),
    (aggregate(op="regex"), "op"),
    (aggregate(metric="sum", field=""), "field is required"),
    (aggregate(metric="count", value="2"), "value must be a number"),
    (aggregate(metric="count", op="starts_with", value="x"),
     "cannot compare the numeric metric"),
    (aggregate(metric="any", op="gt", value=True),
     "cannot compare the boolean metric"),
    (aggregate(metric="any", op="eq", value=1),
     "value must be a boolean"),
    (aggregate(metric="count", op="in", value=["a"]),
     "list of numbers"),
    (aggregate(where={"all": []}), "where"),
    ({"relation": "passes", "object_type": "TollStation",
      "metric": "count", "op": "gt"}, "requires a 'value'"),
])
def test_invalid_aggregate_definitions_are_reported(definition, fragment):
    problems = validate_aggregate(definition)
    assert problems
    assert any(fragment in problem for problem in problems), problems


def test_invalid_aggregate_is_rejected_by_rule_definition():
    rule = make_rule(aggregate(metric="median"))
    problems = validate_rule_definition(rule, VALID_SPEC)
    assert any("metric" in problem for problem in problems)


# ----------------------------------------------------------------------
# engine: metrics
# ----------------------------------------------------------------------


def test_constraint_rule_is_not_aggregate():
    rule = make_rule(aggregate(), kind="constraint")
    assert check(rule) is None


def test_count_metric_and_violation_shape():
    result = check(make_rule(aggregate()))

    assert result is not None
    assert result.passed is False
    assert result.evaluated_rules == 1
    violation = result.violations[0]
    assert violation["object_id"] == "V001"
    assert violation["field"] == "aggregate.passes"
    assert violation["message"] == "聚合约束失败"
    assert violation["detail"]["metric"] == "count"
    assert violation["detail"]["actual"] == 3


def test_count_comparison_that_does_not_hold():
    result = check(make_rule(aggregate(op="lt", value=3)))
    assert result.passed is True and result.violations == []


@pytest.mark.parametrize(("metric", "op", "value", "actual"), [
    ("sum", "eq", 12, 12),
    ("avg", "eq", 4, 4),
    ("min", "eq", -3, -3),
    ("max", "eq", 10, 10),
    ("sum", "gt", 11, 12),
    ("avg", "lt", 5, 4),
])
def test_numeric_metrics(metric, op, value, actual):
    result = check(make_rule(aggregate(
        metric=metric, field="value", op=op, value=value)))

    violation = result.violations[0]
    assert violation["detail"]["metric"] == metric
    assert violation["detail"]["actual"] == actual


def test_sum_skips_non_numeric_field_values():
    children = [
        {"type": "TollStation", "id": "S1", "properties": {"value": 2}},
        {"type": "TollStation", "id": "S2", "properties": {"value": "n/a"}},
        {"type": "TollStation", "id": "S3", "properties": {}},
    ]
    result = check(make_rule(aggregate(metric="sum", field="value",
                                       op="eq", value=2)), children)
    assert result.violations


def test_any_and_all_with_and_without_where():
    congested = {"field": "status", "op": "eq", "value": "congested"}

    any_ok = check(make_rule(aggregate(metric="any", op="eq", value=True,
                                       where=congested)))
    assert any_ok.violations

    all_fail = check(make_rule(aggregate(metric="all", op="eq", value=True,
                                         where=congested)))
    assert all_fail.violations == []

    only_one = [CHILDREN[0]]
    all_one = check(make_rule(aggregate(metric="all", op="eq", value=True,
                                        where=congested)), only_one)
    assert all_one.violations


def test_where_filters_children_for_numeric_metrics():
    result = check(make_rule(aggregate(
        metric="sum", field="value", op="eq", value=10,
        where={"field": "status", "op": "eq", "value": "congested"})))
    assert result.violations[0]["detail"]["actual"] == 10


# ----------------------------------------------------------------------
# engine: empty-set semantics, missing data, skip paths
# ----------------------------------------------------------------------


def test_empty_set_semantics():
    count = check(make_rule(aggregate(metric="count", op="eq", value=0)), [])
    assert count.violations

    sum_zero = check(make_rule(aggregate(metric="sum", field="value",
                                         op="eq", value=0)), [])
    assert sum_zero.violations

    any_false = check(make_rule(aggregate(metric="any", op="eq",
                                          value=False)), [])
    assert any_false.violations

    all_true = check(make_rule(aggregate(metric="all", op="eq",
                                         value=True)), [])
    assert all_true.violations

    for metric in ("avg", "min", "max"):
        empty = check(make_rule(aggregate(metric=metric, field="value",
                                          op="eq", value=0)), [])
        assert empty.violations == [], metric
        assert empty.passed is True


def test_empty_set_is_missing_not_equal_for_negative_operators():
    result = check(make_rule(aggregate(metric="avg", field="value",
                                       op="ne", value=0)), [])
    assert result.violations == []


def test_children_missing_the_reduction_field_are_empty_for_sum():
    result = check(make_rule(aggregate(metric="sum", field="absent",
                                       op="eq", value=0)))
    assert result.violations


def test_disabled_rule_and_when_gate_skip():
    disabled = make_rule(aggregate(), enabled=False)
    result = check(disabled)
    assert result.skipped == 1 and result.evaluated_rules == 0

    gated = make_rule(aggregate(), when={"field": "plate", "op": "eq",
                                         "value": "OTHER"})
    result = check(gated)
    assert result.skipped == 1 and result.violations == []


def test_relation_override_shapes_the_field_label():
    result = check(make_rule(aggregate()), relation="other_rel")
    assert result.violations[0]["field"] == "aggregate.other_rel"


def test_default_message_names_the_metric_and_result():
    rule = make_rule(aggregate(metric="count", op="eq", value=0), message="")
    result = check(rule, [])
    message = result.violations[0]["message"]
    assert "count" in message and "eq" in message and "0" in message


def test_invalid_aggregate_raises_at_evaluation_time():
    rule = make_rule(aggregate(metric="median"))
    with pytest.raises(ValidationError, match="invalid aggregate"):
        check(rule)


# ----------------------------------------------------------------------
# use-case integration: real kernel relation
# ----------------------------------------------------------------------


def create_aggregate(platform, workspace_id, definition=None, **overrides):
    rule = platform.rules.create(
        workspace_id, "admin",
        name="跨对象规则",
        object_type="Vehicle",
        condition={},
        message="跨对象约束失败",
        kind="aggregate",
        aggregate=definition if definition is not None else aggregate(),
        **overrides)
    return platform.rules.publish(workspace_id, "admin", rule["id"])


def test_real_kernel_relation_count(platform, workspace_id):
    create_aggregate(
        platform, workspace_id,
        aggregate(metric="count", op="gte", value=2))

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")

    assert result["passed"] is False
    assert result["object_count"] == 2
    assert result["evaluated_rules"] == 2
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    # V001 passes ST001 + ST002; V002 passes only ST001
    assert violation["object_id"] == "V001"
    assert violation["field"] == "aggregate.passes"
    assert violation["detail"]["actual"] == 2


def test_real_kernel_where_filters_stations(platform, workspace_id):
    create_aggregate(
        platform, workspace_id,
        aggregate(metric="count", op="gte", value=2,
                  where={"field": "status", "op": "eq",
                         "value": "congested"}))

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")
    assert result["passed"] is True
    assert result["violations"] == []


def test_aggregate_and_constraint_rules_coexist(platform, workspace_id):
    create_aggregate(platform, workspace_id)
    rule = platform.rules.create(
        workspace_id, "admin", name="低余额", object_type="Vehicle",
        condition={"field": "balance", "op": "lt", "value": 25},
        message="低余额")
    platform.rules.publish(workspace_id, "admin", rule["id"])

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")

    assert result["rule_count"] == 2
    assert {row["rule_name"] for row in result["violations"]} == {
        "跨对象规则", "低余额"}
    assert result["by_severity"]["error"] == 2


def test_aggregate_disables_index_pruning(platform, workspace_id):
    create_aggregate(platform, workspace_id)

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle", index=True)

    assert result["evaluated_objects"] == 2  # every parent must be checked
    assert result["index_stats"]["pruned"] is False
    assert result["index_stats"]["rules_scanned"] == 1
    entries = [entry for entry in platform.ctx.audit.list(workspace_id, 500)
               if entry["action"] == "rule.evaluate"]
    assert entries[-1]["detail"]["aggregate_rules"] == 1


# ----------------------------------------------------------------------
# use-case integration: duck-typed gateway for relation/child edge cases
# ----------------------------------------------------------------------


class DuckGateway:
    def __init__(self, objects, relations=None, related_error=False):
        self.objects = {f"{obj['type']}/{obj['id']}": obj for obj in objects}
        self.relations = relations or {}
        self.related_error = related_error

    def find_objects(self, object_type, filters=None, report=False):
        return [obj for obj in self.objects.values()
                if obj["type"] == object_type]

    def get_object(self, ref):
        return self.objects.get(ref)

    def related(self, ref, relation, direction="out"):
        if self.related_error:
            raise KeyError(relation)
        return list(self.relations.get((ref, relation), []))


class DuckProvider:
    def __init__(self, gateway):
        self.gateway = gateway

    def for_workspace(self, workspace_id, config):
        return self.gateway


def use_gateway(monkeypatch, platform, gateway) -> None:
    monkeypatch.setattr(platform.ctx, "gateways", DuckProvider(gateway))


def duck_objects() -> list[dict]:
    return [
        {"type": "Vehicle", "id": "P1", "properties": {"plate": "A"}},
        {"type": "Vehicle", "id": "P2", "properties": {"plate": "B"}},
        {"type": "TollStation", "id": "A",
         "properties": {"value": 10, "status": "ok"}},
        {"type": "TollStation", "id": "B",
         "properties": {"value": 5, "status": "ok"}},
        {"type": "TollStation", "id": "C",
         "properties": {"value": 10, "status": "ok"}},
    ]


def duck_relations() -> dict:
    return {
        ("Vehicle/P1", "passes"): ["TollStation/A", "TollStation/B"],
        ("Vehicle/P2", "passes"): ["TollStation/C"],
    }


def test_duck_gateway_sum_across_children(monkeypatch, platform,
                                          workspace_id):
    use_gateway(monkeypatch, platform,
                DuckGateway(duck_objects(), duck_relations()))
    create_aggregate(platform, workspace_id,
                     aggregate(metric="sum", field="value", op="gte",
                               value=15))

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")

    assert [row["object_id"] for row in result["violations"]] == ["P1"]
    assert result["violations"][0]["detail"]["actual"] == 15


def test_duck_gateway_avg_and_max(monkeypatch, platform, workspace_id):
    use_gateway(monkeypatch, platform,
                DuckGateway(duck_objects(), duck_relations()))
    create_aggregate(platform, workspace_id,
                     aggregate(metric="avg", field="value", op="eq",
                               value=7.5))

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")
    assert [row["object_id"] for row in result["violations"]] == ["P1"]


def test_duck_gateway_missing_relation_is_an_empty_set(monkeypatch, platform,
                                                       workspace_id):
    use_gateway(monkeypatch, platform,
                DuckGateway(duck_objects(), {}, related_error=True))
    create_aggregate(platform, workspace_id,
                     aggregate(metric="count", op="eq", value=0))

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")

    assert len(result["violations"]) == 2
    assert result["passed"] is False


def test_duck_gateway_missing_child_object_is_skipped(monkeypatch, platform,
                                                      workspace_id):
    use_gateway(monkeypatch, platform, DuckGateway(
        duck_objects(),
        {("Vehicle/P1", "passes"): ["TollStation/GHOST"],
         ("Vehicle/P2", "passes"): ["TollStation/C"]}))
    create_aggregate(platform, workspace_id,
                     aggregate(metric="count", op="eq", value=0))

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle")

    assert [row["object_id"] for row in result["violations"]] == ["P1"]


def test_simulate_ignores_aggregate_rules_without_crashing(platform,
                                                           workspace_id):
    # Explicitly out of scope for P2.5: single-object simulation must stay
    # usable in a workspace that also carries an aggregate rule.
    create_aggregate(platform, workspace_id)

    result = platform.rules.simulate(
        workspace_id, "admin", "Vehicle/V001", {"balance": 1})

    assert result["before"]["violations"] == []
    assert result["after"]["violations"] == []
    assert result["delta"]["changed"] is False
