"""P2.5: optional rule-condition field index (candidate pruning).

The index is a per-evaluation in-memory posting list - there is no persistent
secondary index in SQLite or the kernel.  These tests pin (a) which predicates
are indexable, (b) the fallback-to-full-scan safety rule, and (c) that
``index=True`` never changes the violation set of ``index=False``.
"""

from __future__ import annotations

from onto_platform.application.rule_index import RuleFieldIndex
from onto_platform.domain.rule import RuleSpec

OBJECTS = [
    {"type": "Vehicle", "id": "V001",
     "properties": {"plate": "鲁A12345", "balance": 100,
                    "address": {"city": "Jinan"}, "tags": ["a", "b"]}},
    {"type": "Vehicle", "id": "V002",
     "properties": {"plate": "鲁B67890", "balance": 20}},
    {"type": "Vehicle", "id": "V003", "properties": {"balance": 5}},
]


class FakeGateway:
    """Only the read surface RuleFieldIndex needs."""

    def __init__(self, objects):
        self.objects = [dict(obj) for obj in objects]

    def find_objects(self, object_type, filters=None, report=False):
        return [obj for obj in self.objects if obj.get("type") == object_type]


def make_spec(condition, **overrides) -> RuleSpec:
    data = {
        "id": "rule_1",
        "workspace_id": "ws",
        "name": "indexed",
        "object_type": "Vehicle",
        "condition": condition,
        "message": "violated",
    }
    data.update(overrides)
    return RuleSpec.from_dict(data)


def idx(rule, objects=None) -> RuleFieldIndex:
    return RuleFieldIndex.build(FakeGateway(OBJECTS if objects is None
                                            else objects), "Vehicle", [rule])


# ----------------------------------------------------------------------
# posting lists
# ----------------------------------------------------------------------


def test_eq_is_indexed_and_candidates_are_exact():
    rule = make_spec({"all": [{"field": "balance", "op": "eq", "value": 20}]})
    index = idx(rule)

    assert index.candidates(rule) == {"V002"}
    assert index.stats() == {"scanned": 3, "fields": 1, "values": 3,
                             "rules_indexed": 1, "rules_scanned": 1}


def test_in_unions_posting_lists():
    rule = make_spec({"all": [
        {"field": "balance", "op": "in", "value": [20, 5]}]})

    assert idx(rule).candidates(rule) == {"V002", "V003"}


def test_contains_starts_with_and_ends_with_match_string_values():
    contains = make_spec({"all": [
        {"field": "plate", "op": "contains", "value": "A123"}]})
    prefix = make_spec({"all": [
        {"field": "plate", "op": "starts_with", "value": "鲁B"}]},
        id="rule_2")
    suffix = make_spec({"all": [
        {"field": "plate", "op": "ends_with", "value": "67890"}]},
        id="rule_3")

    index = RuleFieldIndex.build(
        FakeGateway(OBJECTS), "Vehicle", [contains, prefix, suffix])
    assert index.candidates(contains) == {"V001"}
    assert index.candidates(prefix) == {"V002"}
    assert index.candidates(suffix) == {"V002"}


def test_dotted_paths_are_indexed():
    rule = make_spec({"all": [
        {"field": "address.city", "op": "eq", "value": "Jinan"}]})

    assert idx(rule).candidates(rule) == {"V001"}


def test_multiple_predicates_intersect():
    rule = make_spec({"all": [
        {"field": "balance", "op": "in", "value": [20, 5]},
        {"field": "plate", "op": "contains", "value": "B"},
    ]})

    assert idx(rule).candidates(rule) == {"V002"}


def test_bare_leaf_condition_is_indexable():
    rule = make_spec({"field": "balance", "op": "eq", "value": 5})

    assert idx(rule).candidates(rule) == {"V003"}


def test_missing_property_never_matches():
    rule = make_spec({"all": [
        {"field": "plate", "op": "eq", "value": "鲁B67890"}]})

    assert idx(rule).candidates(rule) == {"V002"}


def test_empty_object_set_yields_empty_candidates():
    rule = make_spec({"all": [{"field": "balance", "op": "eq", "value": 1}]})
    index = idx(rule, objects=[])

    assert index.candidates(rule) == set()
    assert index.stats()["scanned"] == 0


# ----------------------------------------------------------------------
# sound fallbacks: never prune what cannot be answered from postings
# ----------------------------------------------------------------------


def test_negative_and_logical_predicates_are_not_indexed():
    operators = make_spec({"all": [
        {"field": "balance", "op": "gt", "value": 0}]})
    negated = make_spec({"not": {"field": "plate", "op": "eq",
                                 "value": "x"}}, id="rule_2")
    any_node = make_spec({"any": [
        {"field": "balance", "op": "eq", "value": 20}]}, id="rule_3")

    index = RuleFieldIndex.build(
        FakeGateway(OBJECTS), "Vehicle", [operators, negated, any_node])

    assert index.candidates(operators) is None
    assert index.candidates(negated) is None
    assert index.candidates(any_node) is None
    assert index.stats()["rules_indexed"] == 0
    assert index.stats()["rules_scanned"] == 3
    assert index.stats()["fields"] == 0


def test_special_fields_are_not_indexed():
    rule = make_spec({"all": [{"field": "$id", "op": "eq", "value": "V001"}]})

    assert idx(rule).candidates(rule) is None


def test_unhashable_values_force_a_full_scan():
    rule = make_spec({"all": [{"field": "tags", "op": "eq", "value": "a"}]})
    index = idx(rule)

    assert index.candidates(rule) is None
    assert index.stats()["fields"] == 1
    assert index.stats()["values"] == 0


# ----------------------------------------------------------------------
# use-case integration (real kernel)
# ----------------------------------------------------------------------


def publish_rule(platform, workspace_id, **params) -> dict:
    rule = platform.rules.create(workspace_id, "admin", **params)
    return platform.rules.publish(workspace_id, "admin", rule["id"])


def test_index_does_not_change_the_violation_set(platform, workspace_id):
    publish_rule(
        platform, workspace_id,
        name="特定车牌与余额",
        object_type="Vehicle",
        condition={"all": [
            {"field": "plate", "op": "eq", "value": "鲁B67890"},
            {"field": "balance", "op": "in", "value": [20]},
        ]},
        message="需要关注")

    full = platform.rules.evaluate(workspace_id, "admin",
                                   object_type="Vehicle")
    pruned = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle", index=True)

    assert "index_stats" not in full
    assert full["violations"] == pruned["violations"]
    assert full["passed"] == pruned["passed"] is False
    assert full["object_count"] == pruned["object_count"] == 2
    assert full["evaluated_objects"] == 2
    assert pruned["evaluated_objects"] == 1
    stats = pruned["index_stats"]
    assert stats["scanned"] == 2
    assert stats["rules_indexed"] == 1
    assert stats["rules_scanned"] == 1
    assert stats["pruned"] is True
    assert stats["candidates"] == 1


def test_negative_rule_falls_back_to_full_scan(platform, workspace_id):
    publish_rule(
        platform, workspace_id,
        name="非鲁A车辆",
        object_type="Vehicle",
        condition={"not": {"field": "plate", "op": "eq",
                           "value": "鲁A12345"}},
        message="非鲁A")

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle", index=True)

    assert result["violations"]
    assert result["evaluated_objects"] == 2
    stats = result["index_stats"]
    assert stats["pruned"] is False
    assert stats["rules_indexed"] == 0
    assert stats["rules_scanned"] == 1
    assert stats["candidates"] == 2


def test_empty_candidate_set_keeps_the_compatible_shape(platform, workspace_id):
    publish_rule(
        platform, workspace_id,
        name="不存在的车牌",
        object_type="Vehicle",
        condition={"all": [
            {"field": "plate", "op": "eq", "value": "NO-SUCH"}]},
        message="不会命中")

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle", index=True)

    assert result["passed"] is True
    assert result["object_count"] == 2  # total stays total
    assert result["total_objects"] == 2
    assert result["evaluated_objects"] == 0
    assert result["violations"] == []
    assert result["has_more"] is False
    assert result["next_offset"] is None
    assert result["rule_count"] == 1
    assert result["by_severity"] == {"error": 0, "warning": 0, "info": 0}
    assert result["index_stats"]["candidates"] == 0
    assert result["index_stats"]["pruned"] is True


def test_indexed_in_rule_prunes_and_evaluates(platform, workspace_id):
    publish_rule(
        platform, workspace_id,
        name="指定余额集合",
        object_type="Vehicle",
        condition={"all": [
            {"field": "balance", "op": "in", "value": [20]},
            {"field": "plate", "op": "starts_with", "value": "鲁B"},
        ]},
        message="命中")

    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle", index=True)

    assert [row["object_id"] for row in result["violations"]] == ["V002"]
    assert result["evaluated_objects"] == 1
    assert result["index_stats"]["fields"] == 2


def test_refs_mode_does_not_build_an_index(platform, workspace_id):
    publish_rule(
        platform, workspace_id,
        name="余额集合",
        object_type="Vehicle",
        condition={"all": [{"field": "balance", "op": "eq", "value": 20}]},
        message="命中")

    result = platform.rules.evaluate(
        workspace_id, "admin", refs=["Vehicle/V001", "Vehicle/V002"],
        index=True)

    assert result["evaluated_objects"] == 2
    stats = result["index_stats"]
    assert stats["mode"] == "refs"
    assert stats["pruned"] is False
    assert stats["scanned"] == 0


def test_no_rules_means_no_pruning(platform, workspace_id):
    result = platform.rules.evaluate(workspace_id, "admin",
                                     object_type="Vehicle", index=True)

    assert result["evaluated_objects"] == 2
    assert result["index_stats"]["rules_scanned"] == 0
    assert result["index_stats"]["pruned"] is False


def test_audit_records_index_usage(platform, workspace_id):
    publish_rule(
        platform, workspace_id,
        name="余额集合",
        object_type="Vehicle",
        condition={"all": [{"field": "balance", "op": "eq", "value": 20}]},
        message="命中")
    platform.rules.evaluate(workspace_id, "admin", object_type="Vehicle",
                            index=True)

    entries = [entry for entry in platform.ctx.audit.list(workspace_id, 500)
               if entry["action"] == "rule.evaluate"]
    detail = entries[-1]["detail"]
    assert detail["index"] is True
    assert detail["indexed_rules"] == 1
    assert detail["aggregate_rules"] == 0
