"""P1-Rule use cases: declare, edit, publish, disable/delete, list, evaluate.

These tests drive the application layer directly against the standard seeded
traffic workspace (``platform`` / ``workspace_id`` fixtures): Vehicle/V001 has
balance 100 and Vehicle/V002 balance 20, both with plate properties.
"""

from __future__ import annotations

import pytest

from onto_platform.application.rule_usecases import RuleUseCases
from onto_platform.errors import (
    AccessDeniedError,
    ConcurrencyConflictError,
    NotFoundError,
    ValidationError,
)


def rules(platform) -> RuleUseCases:
    return RuleUseCases(platform.ctx)


def make_rule(platform, workspace_id, **overrides) -> dict:
    params = {
        "name": "余额非负",
        "object_type": "Vehicle",
        "condition": {"field": "balance", "op": "lt", "value": 0},
        "message": "余额不能为负",
    }
    params.update(overrides)
    return rules(platform).create(workspace_id, "admin", **params)


def add_viewer(platform, workspace_id, actor="bob") -> None:
    platform.workspaces.add_member(workspace_id, actor, "viewer", "admin")


def audit_actions(platform, workspace_id, action: str) -> list[dict]:
    return [entry for entry in platform.ctx.audit.list(workspace_id, limit=500)
            if entry["action"] == action]


def stored_rule(platform, workspace_id, rule_id):
    return platform.ctx.repo.get("rule", workspace_id, rule_id)


# ----------------------------------------------------------------------
# create
# ----------------------------------------------------------------------


def test_create_draft_rule(platform, workspace_id):
    rule = make_rule(
        platform, workspace_id,
        description="余额字段的完整性约束",
        severity="warning",
        priority=20,
        when={"field": "plate", "op": "not_null"})

    assert rule["id"]
    assert rule["workspace_id"] == workspace_id
    assert rule["name"] == "余额非负"
    assert rule["description"] == "余额字段的完整性约束"
    assert rule["object_type"] == "Vehicle"
    assert rule["kind"] == "constraint"
    assert rule["severity"] == "warning"
    assert rule["priority"] == 20
    assert rule["enabled"] is True
    assert rule["lifecycle"] == "draft"
    assert rule["version"] == 1
    assert rule["condition"] == {"field": "balance", "op": "lt", "value": 0}
    assert rule["when"] == {"field": "plate", "op": "not_null"}
    assert rule["created_at"] and rule["updated_at"]

    stored = stored_rule(platform, workspace_id, rule["id"])
    assert stored is not None
    assert stored["condition"] == rule["condition"]

    entries = audit_actions(platform, workspace_id, "rule.create")
    assert entries and entries[-1]["target"] == rule["id"]
    assert entries[-1]["detail"]["object_type"] == "Vehicle"


def test_create_rule_defaults(platform, workspace_id):
    rule = make_rule(platform, workspace_id)
    assert rule["severity"] == "error"
    assert rule["priority"] == 100
    assert rule["description"] == ""
    assert rule["when"] == {}


@pytest.mark.parametrize(("overrides", "fragment"), [
    ({"object_type": "Ghost"}, "not defined in the ontology"),
    ({"condition": {"field": "ghost", "op": "eq", "value": 1}},
     "is not a property defined in the current ontology"),
    ({"condition": {"field": "balance", "op": "regex", "value": "x"}},
     "not supported"),
    ({"name": "  "}, "name must not be empty"),
    ({"message": ""}, "message must not be empty"),
    ({"severity": "fatal"}, "severity"),
    ({"priority": True}, "priority must be an int"),
])
def test_create_rule_rejects_invalid_definitions(platform, workspace_id,
                                                 overrides, fragment):
    with pytest.raises(ValidationError) as excinfo:
        make_rule(platform, workspace_id, **overrides)
    assert fragment in str(excinfo.value)
    assert platform.ctx.repo.list("rule", workspace_id) == []


def test_create_rule_requires_editor_role(platform, workspace_id):
    add_viewer(platform, workspace_id)
    with pytest.raises(AccessDeniedError):
        rules(platform).create(
            workspace_id, "bob", name="x", object_type="Vehicle",
            condition={"field": "balance", "op": "gte", "value": 0},
            message="m")
    with pytest.raises(AccessDeniedError):
        rules(platform).create(
            workspace_id, "stranger", name="x", object_type="Vehicle",
            condition={"field": "balance", "op": "gte", "value": 0},
            message="m")


# ----------------------------------------------------------------------
# update
# ----------------------------------------------------------------------


def test_update_draft_rule(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    updated = rules(platform).update(
        workspace_id, "admin", created["id"],
        name="  余额必须非负  ",
        description="更新后的说明",
        severity="warning",
        priority=5,
        condition={"field": "balance", "op": "gt", "value": 0})

    assert updated["id"] == created["id"]
    assert updated["name"] == "  余额必须非负  "
    assert updated["description"] == "更新后的说明"
    assert updated["severity"] == "warning"
    assert updated["priority"] == 5
    assert updated["condition"] == {"field": "balance", "op": "gt", "value": 0}
    assert updated["version"] == 2
    assert updated["lifecycle"] == "draft"

    stored = stored_rule(platform, workspace_id, created["id"])
    assert stored["condition"] == updated["condition"]
    entries = audit_actions(platform, workspace_id, "rule.update")
    assert entries and entries[-1]["detail"]["fields"] == [
        "condition", "description", "name", "priority", "severity"]


def test_update_requires_at_least_one_field(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    with pytest.raises(ValidationError) as excinfo:
        rules(platform).update(workspace_id, "admin", created["id"])
    assert "nothing to update" in str(excinfo.value)


def test_update_published_rule_is_rejected(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    rules(platform).publish(workspace_id, "admin", created["id"])
    with pytest.raises(ValidationError) as excinfo:
        rules(platform).update(workspace_id, "admin", created["id"],
                               name="new name")
    assert "only a draft rule can be edited" in str(excinfo.value)


def test_update_uses_optimistic_lock(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    current, version = platform.ctx.load_versioned(
        "rule", workspace_id, created["id"])

    # an out-of-band write bumps the aggregate version behind our back
    platform.ctx.save("rule", {**current, "name": "外部改动"})

    with pytest.raises(ConcurrencyConflictError):
        rules(platform).update(workspace_id, "admin", created["id"],
                               name="我的改动", expected_version=version)


def test_update_unknown_rule_is_not_found(platform, workspace_id):
    with pytest.raises(NotFoundError):
        rules(platform).update(workspace_id, "admin", "rule_missing",
                               name="x")


# ----------------------------------------------------------------------
# publish
# ----------------------------------------------------------------------


def test_publish_draft_rule(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    published = rules(platform).publish(workspace_id, "admin", created["id"])

    assert published["id"] == created["id"]
    assert published["lifecycle"] == "published"
    assert published["version"] == 2
    assert published["enabled"] is True
    stored = stored_rule(platform, workspace_id, created["id"])
    assert stored["lifecycle"] == "published"

    entries = audit_actions(platform, workspace_id, "rule.publish")
    assert entries and entries[-1]["target"] == created["id"]


def test_publish_is_idempotent(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    first = rules(platform).publish(workspace_id, "admin", created["id"])
    second = rules(platform).publish(workspace_id, "admin", created["id"])

    assert second == first
    assert second["version"] == first["version"]
    assert len(audit_actions(platform, workspace_id, "rule.publish")) == 1


def test_archived_rule_cannot_be_published(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    rules(platform).publish(workspace_id, "admin", created["id"])
    rules(platform).delete(workspace_id, "admin", created["id"])
    with pytest.raises(ValidationError):
        rules(platform).publish(workspace_id, "admin", created["id"])


def test_publish_revalidates_against_the_live_ontology(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    # the draft now targets a property the ontology no longer has
    doc = stored_rule(platform, workspace_id, created["id"])
    doc["condition"] = {"field": "ghost", "op": "eq", "value": 1}
    platform.ctx.save("rule", doc)
    with pytest.raises(ValidationError) as excinfo:
        rules(platform).publish(workspace_id, "admin", created["id"])
    assert "is not a property" in str(excinfo.value)


# ----------------------------------------------------------------------
# disable / delete
# ----------------------------------------------------------------------


def test_disable_rule_is_idempotent(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    rules(platform).publish(workspace_id, "admin", created["id"])
    disabled = rules(platform).disable(workspace_id, "admin", created["id"])
    assert disabled["enabled"] is False
    assert disabled["lifecycle"] == "published"
    again = rules(platform).disable(workspace_id, "admin", created["id"])
    assert again == disabled
    assert len(audit_actions(platform, workspace_id, "rule.disable")) == 1


def test_delete_draft_rule_removes_document(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    result = rules(platform).delete(workspace_id, "admin", created["id"])
    assert result["deleted"] is True
    assert result["archived"] is False
    assert stored_rule(platform, workspace_id, created["id"]) is None
    assert audit_actions(platform, workspace_id, "rule.delete")


def test_delete_published_rule_archives_it(platform, workspace_id):
    created = make_rule(platform, workspace_id)
    rules(platform).publish(workspace_id, "admin", created["id"])
    result = rules(platform).delete(workspace_id, "admin", created["id"])

    assert result["deleted"] is False
    assert result["archived"] is True
    stored = stored_rule(platform, workspace_id, created["id"])
    assert stored["lifecycle"] == "archived"
    assert stored["enabled"] is False

    archived = rules(platform).list(workspace_id, "admin",
                                    lifecycle="archived")
    assert [rule["id"] for rule in archived] == [created["id"]]
    # deleting an archived rule is idempotent
    again = rules(platform).delete(workspace_id, "admin", created["id"])
    assert again["archived"] is True


# ----------------------------------------------------------------------
# list
# ----------------------------------------------------------------------


def test_list_rules_is_deterministic(platform, workspace_id):
    third = make_rule(platform, workspace_id, name="C", priority=30)
    first = make_rule(platform, workspace_id, name="A", priority=10)
    second = make_rule(platform, workspace_id, name="B", priority=20)

    listed = rules(platform).list(workspace_id, "admin")
    assert [rule["id"] for rule in listed] == [
        first["id"], second["id"], third["id"]]
    assert [rule["priority"] for rule in listed] == [10, 20, 30]
    # round-tripped through RuleSpec: the stored shape is stable
    assert listed[0]["condition"] == stored_rule(
        platform, workspace_id, first["id"])["condition"]

    drafts = rules(platform).list(workspace_id, "admin", lifecycle="draft")
    assert len(drafts) == 3
    rules(platform).publish(workspace_id, "admin", first["id"])
    published = rules(platform).list(workspace_id, "admin",
                                     lifecycle="published")
    assert [rule["id"] for rule in published] == [first["id"]]


def test_list_rules_rejects_unknown_lifecycle(platform, workspace_id):
    with pytest.raises(ValidationError):
        rules(platform).list(workspace_id, "admin", lifecycle="deleted")


def test_viewer_can_list_but_not_write(platform, workspace_id):
    make_rule(platform, workspace_id)
    add_viewer(platform, workspace_id)
    assert len(rules(platform).list(workspace_id, "bob")) == 1
    with pytest.raises(AccessDeniedError):
        rules(platform).delete(workspace_id, "bob",
                               platform.ctx.repo.list("rule", workspace_id)[0]["id"])


def test_rule_usecases_facade_exposes_all_operations(platform):
    facade = rules(platform)
    for attribute in ("create", "update", "publish", "disable", "delete",
                      "list", "evaluate"):
        assert callable(getattr(facade, attribute))


# ----------------------------------------------------------------------
# evaluate
# ----------------------------------------------------------------------


def _published(platform, workspace_id, **overrides) -> dict:
    created = make_rule(platform, workspace_id, **overrides)
    return rules(platform).publish(workspace_id, "admin", created["id"])


def test_evaluate_all_objects_of_a_type(platform, workspace_id):
    _published(platform, workspace_id)  # balance < 0 fires for nobody
    result = rules(platform).evaluate(workspace_id, "admin",
                                      object_type="Vehicle")

    assert result["passed"] is True
    assert result["object_count"] == 2
    assert result["rule_count"] == 1
    assert result["violations"] == []
    assert result["missing"] == []
    assert result["by_severity"] == {"error": 0, "warning": 0, "info": 0}

    entries = audit_actions(platform, workspace_id, "rule.evaluate")
    assert entries
    detail = entries[-1]["detail"]
    assert detail["object_count"] == 2
    assert detail["rule_count"] == 1
    assert detail["violations"] == 0


def test_evaluate_reports_error_violation(platform, workspace_id):
    _published(
        platform, workspace_id,
        name="低余额告警",
        condition={"field": "balance", "op": "lt", "value": 25},
        message="余额低于 25")
    result = rules(platform).evaluate(workspace_id, "admin",
                                      object_type="Vehicle")

    assert result["passed"] is False
    assert result["object_count"] == 2
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["object_id"] == "V002"
    assert violation["object_type"] == "Vehicle"
    assert violation["severity"] == "error"
    assert violation["message"] == "余额低于 25"
    assert result["by_severity"]["error"] == 1


def test_evaluate_warning_does_not_fail(platform, workspace_id):
    _published(
        platform, workspace_id, severity="warning",
        condition={"field": "balance", "op": "lt", "value": 25},
        message="余额偏低")
    result = rules(platform).evaluate(workspace_id, "admin",
                                      object_type="Vehicle")
    assert result["passed"] is True
    assert len(result["violations"]) == 1
    assert result["violations"][0]["severity"] == "warning"
    assert result["by_severity"]["warning"] == 1


def test_evaluate_by_refs_and_missing_refs(platform, workspace_id):
    _published(platform, workspace_id)
    result = rules(platform).evaluate(
        workspace_id, "admin", refs=["Vehicle/V001", "Vehicle/V999"])

    assert result["object_count"] == 1
    assert result["missing"] == ["Vehicle/V999"]
    assert result["rule_count"] == 1
    assert result["passed"] is True


def test_evaluate_refs_restrict_objects(platform, workspace_id):
    _published(
        platform, workspace_id,
        condition={"field": "balance", "op": "lt", "value": 25},
        message="余额低于 25")
    result = rules(platform).evaluate(workspace_id, "admin",
                                      refs=["Vehicle/V001"])
    assert result["object_count"] == 1
    assert result["violations"] == []
    assert result["passed"] is True


@pytest.mark.parametrize("kwargs", [
    {},
    {"refs": []},
    {"refs": ["not-a-ref"]},
    {"refs": "Vehicle/V001"},
    {"object_type": "Ghost"},
])
def test_evaluate_rejects_bad_arguments(platform, workspace_id, kwargs):
    with pytest.raises(ValidationError):
        rules(platform).evaluate(workspace_id, "admin", **kwargs)


def test_evaluate_only_published_rules_by_default(platform, workspace_id):
    # a draft rule must not affect the serving evaluation ...
    make_rule(
        platform, workspace_id,
        condition={"field": "balance", "op": "lt", "value": 25},
        message="余额低于 25")
    draft_only = rules(platform).evaluate(workspace_id, "admin",
                                          object_type="Vehicle")
    assert draft_only["rule_count"] == 0
    assert draft_only["violations"] == []
    assert draft_only["passed"] is True

    # ... but include_drafts previews it (editing/test mode)
    preview = rules(platform).evaluate(workspace_id, "admin",
                                       object_type="Vehicle",
                                       include_drafts=True)
    assert preview["rule_count"] == 1
    assert preview["violations"][0]["object_id"] == "V002"


def test_evaluate_skips_disabled_and_when_skipped_rules(platform, workspace_id):
    disabled = _published(
        platform, workspace_id,
        condition={"field": "balance", "op": "lt", "value": 25},
        message="禁用规则")
    rules(platform).disable(workspace_id, "admin", disabled["id"])
    _published(
        platform, workspace_id,
        name="余额为负才报警",
        condition={"field": "balance", "op": "lt", "value": 0},
        message="余额为负",
        when={"field": "balance", "op": "gt", "value": 50})
    result = rules(platform).evaluate(workspace_id, "admin",
                                      object_type="Vehicle")
    assert result["rule_count"] == 1          # disabled rule is filtered out
    assert result["skipped"] == 1             # when precondition skips V002
    assert result["evaluated_rules"] == 1     # only V001 gets evaluated
    assert result["violations"] == []


def test_evaluate_filters_rules_by_object_type(platform, workspace_id):
    _published(platform, workspace_id)  # Vehicle rule
    _published(
        platform, workspace_id, name="收费站拥堵", object_type="TollStation",
        condition={"field": "status", "op": "eq", "value": "congested"},
        message="收费站拥堵")
    vehicle = rules(platform).evaluate(workspace_id, "admin",
                                       object_type="Vehicle")
    assert vehicle["rule_count"] == 1
    assert vehicle["object_count"] == 2

    station = rules(platform).evaluate(workspace_id, "admin",
                                       object_type="TollStation")
    assert station["rule_count"] == 1
    assert station["object_count"] == 2
    assert station["passed"] is False
    assert {item["object_id"] for item in station["violations"]} == {"ST001"}


def test_evaluate_orders_violations_by_priority(platform, workspace_id):
    _published(
        platform, workspace_id, name="低余额", priority=20,
        condition={"field": "balance", "op": "lt", "value": 25},
        message="余额低于 25")
    _published(
        platform, workspace_id, name="低余额告警", priority=10,
        condition={"field": "balance", "op": "lt", "value": 25},
        message="余额低于 25（高优先级）")
    result = rules(platform).evaluate(workspace_id, "admin",
                                      object_type="Vehicle")
    assert [item["rule_name"] for item in result["violations"]] == [
        "低余额告警", "低余额"]


def test_evaluate_requires_viewer_role(platform, workspace_id):
    _published(platform, workspace_id)
    add_viewer(platform, workspace_id)
    assert rules(platform).evaluate(workspace_id, "bob",
                                    object_type="Vehicle")["object_count"] == 2
    with pytest.raises(AccessDeniedError):
        rules(platform).evaluate(workspace_id, "stranger",
                                 object_type="Vehicle")


def test_evaluate_without_any_ontology_fails_cleanly(platform):
    workspace = platform.workspaces.create("空工作区", actor="admin")
    with pytest.raises(ValidationError) as excinfo:
        rules(platform).evaluate(workspace["id"], "admin",
                                 object_type="Vehicle")
    assert "ontology" in str(excinfo.value)


def test_create_rule_without_ontology_fails_cleanly(platform):
    workspace = platform.workspaces.create("空工作区", actor="admin")
    with pytest.raises(ValidationError) as excinfo:
        rules(platform).create(
            workspace["id"], "admin", name="x", object_type="Vehicle",
            condition={"field": "balance", "op": "gte", "value": 0},
            message="m")
    assert "ontology" in str(excinfo.value)
