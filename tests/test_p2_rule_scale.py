"""P2-C: rule evaluation pagination and dotted property paths.

P1 rule conditions addressed a flat property name.  Business objects routinely
carry nested JSON (an address, an inspection result) and large object types
cannot be evaluated in one unbounded response.  These tests pin the additive
contract: dotted paths resolve inside ``properties`` with the same MISSING
semantics, and every evaluation is a stable page.
"""

from __future__ import annotations

import pytest

from onto_platform.domain.rule import evaluate_condition, validate_condition
from onto_platform.errors import ValidationError


def _published_rule(platform, wid, **overrides) -> dict:
    params = {
        "name": "All vehicles",
        "object_type": "Vehicle",
        "condition": {"field": "balance", "op": "gte", "value": 0},
        "message": "vehicle has a balance",
    }
    params.update(overrides)
    rule = platform.rules.create(wid, "admin", **params)
    return platform.rules.publish(wid, "admin", rule["id"])


# ----------------------------------------------------------------------
# dotted property paths
# ----------------------------------------------------------------------


def test_nested_path_resolves_inside_properties():
    obj = {"id": "V1", "type": "Vehicle",
           "properties": {"address": {"city": "Jinan", "geo": {"lat": 1}}}}

    assert evaluate_condition(
        {"field": "address.city", "op": "eq", "value": "Jinan"}, obj) is True
    assert evaluate_condition(
        {"field": "address.geo.lat", "op": "gte", "value": 1}, obj) is True
    assert evaluate_condition(
        {"field": "address.city", "op": "eq", "value": "Qingdao"}, obj) is False


def test_nested_missing_path_keeps_missing_semantics():
    obj = {"id": "V1", "type": "Vehicle", "properties": {"address": {}}}

    assert evaluate_condition(
        {"field": "address.city", "op": "eq", "value": "Jinan"}, obj) is False
    assert evaluate_condition(
        {"field": "address.city", "op": "is_null"}, obj) is True
    assert evaluate_condition(
        {"field": "address.city", "op": "not_null"}, obj) is False
    # a scalar midway through the path is "missing", never an exception
    assert evaluate_condition(
        {"field": "address.city.name", "op": "eq", "value": "x"},
        {"properties": {"address": {"city": "Jinan"}}}) is False


def test_dotted_path_validation_uses_the_root_property():
    condition = {"field": "address.city", "op": "eq", "value": "Jinan"}

    assert validate_condition(condition, properties={"address"}) == []
    problems = validate_condition(condition, properties={"plate"})
    assert problems and "address.city" in problems[0]
    # without an ontology hint evaluation-time validation skips existence
    assert validate_condition(condition) == []


@pytest.mark.parametrize("field", ["address..city", ".address", "address.",
                                   "address.city."])
def test_dotted_path_rejects_empty_segments(field):
    problems = validate_condition(
        {"field": field, "op": "eq", "value": "x"}, properties={"address"})
    assert any("empty segments" in problem for problem in problems)


def test_special_fields_stay_exact():
    assert evaluate_condition(
        {"field": "$id", "op": "eq", "value": "V1"},
        {"id": "V1", "type": "Vehicle", "properties": {}}) is True
    problems = validate_condition(
        {"field": "$id.name", "op": "eq", "value": "x"},
        properties={"address"})
    assert any("$id.name" in problem for problem in problems)


# ----------------------------------------------------------------------
# pagination
# ----------------------------------------------------------------------


def test_object_type_pagination_is_stable_and_reports_has_more(
        platform, workspace_id):
    _published_rule(platform, workspace_id)

    first = platform.rules.evaluate(
        workspace_id, "admin", object_type="Vehicle", limit=1, offset=0)

    assert first["total_objects"] == 2
    assert first["object_count"] == 2  # backward-compatible total
    assert first["evaluated_objects"] == 1
    assert first["offset"] == 0
    assert first["limit"] == 1
    assert first["has_more"] is True
    assert first["next_offset"] == 1
    assert [row["object_id"] for row in first["violations"]] == ["V001"]

    second = platform.rules.evaluate(
        workspace_id, "admin", object_type="Vehicle", limit=1, offset=1)

    assert second["evaluated_objects"] == 1
    assert second["has_more"] is False
    assert second["next_offset"] is None
    assert [row["object_id"] for row in second["violations"]] == ["V002"]

    empty = platform.rules.evaluate(
        workspace_id, "admin", object_type="Vehicle", limit=1, offset=2)
    assert empty["evaluated_objects"] == 0
    assert empty["violations"] == []
    assert empty["has_more"] is False
    assert empty["next_offset"] is None


def test_refs_mode_preserves_input_order_across_pages(platform, workspace_id):
    _published_rule(platform, workspace_id)
    refs = ["Vehicle/V002", "Vehicle/V001"]

    page = platform.rules.evaluate(
        workspace_id, "admin", refs=refs, limit=1, offset=1)

    assert page["total_objects"] == 2
    assert page["evaluated_objects"] == 1
    assert [row["object_id"] for row in page["violations"]] == ["V001"]


def test_evaluate_records_page_in_audit(platform, workspace_id):
    _published_rule(platform, workspace_id)
    platform.rules.evaluate(
        workspace_id, "admin", object_type="Vehicle", limit=1, offset=0)

    entries = [entry for entry in platform.ctx.audit.list(workspace_id, 500)
               if entry["action"] == "rule.evaluate"]
    detail = entries[-1]["detail"]
    assert detail["object_count"] == 2
    assert detail["evaluated_objects"] == 1
    assert detail["limit"] == 1
    assert detail["offset"] == 0
    assert detail["has_more"] is True


@pytest.mark.parametrize("limit", [0, -1, 1001, "1", True])
def test_invalid_limit_is_rejected(platform, workspace_id, limit):
    with pytest.raises(ValidationError, match="limit"):
        platform.rules.evaluate(
            workspace_id, "admin", object_type="Vehicle", limit=limit)


@pytest.mark.parametrize("offset", [-1, "0", True])
def test_invalid_offset_is_rejected(platform, workspace_id, offset):
    with pytest.raises(ValidationError, match="offset"):
        platform.rules.evaluate(
            workspace_id, "admin", object_type="Vehicle", offset=offset)
