"""P2 Digital Twin service: snapshot, propagation, counterfactuals, scenarios.

These tests drive :class:`DigitalTwinService` against the standard seeded
traffic workspace:

* ``Vehicle/V001`` (plate 鲁A12345, balance 100) passes ``ST001`` and ``ST002``;
* ``Vehicle/V002`` (plate 鲁B67890, balance 20) passes ``ST001``;
* ``passes`` is declared ``Vehicle -> TollStation`` in the published ontology.

Snapshot, propagation and rule evaluation each have at least one case that
goes through the real kernel gateway; only the cycle-safety test wraps the
gateway (with ``monkeypatch``) to inject a cycle that the seeded ontology
cannot express, per the task's allowance.
"""

from __future__ import annotations

import pytest

from onto_platform.application.digital_twin import (
    DEFAULT_DEPTH,
    DigitalTwinService,
    MAX_DEPTH,
    MAX_NODES,
    MAX_SCENARIOS,
    TWIN_MODES,
    TWIN_POLICY_KIND,
)
from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def twin(platform) -> DigitalTwinService:
    return DigitalTwinService(platform.ctx)


def gateway(platform, workspace_id):
    return platform.ctx.gateways.for_workspace(
        workspace_id, platform.ctx.default_runtime_config)


def publish_vehicle_rule(platform, workspace_id, **overrides) -> dict:
    params = {
        "name": "balance floor",
        "object_type": "Vehicle",
        "condition": {"field": "balance", "op": "lt", "value": 50},
        "message": "balance below 50",
    }
    params.update(overrides)
    created = platform.rules.create(workspace_id, "admin", **params)
    return platform.rules.publish(workspace_id, "admin", created["id"])


def audit_actions(platform, workspace_id, action: str) -> list[dict]:
    return [entry for entry in platform.ctx.audit.list(workspace_id, limit=500)
            if entry["action"] == action]


def add_viewer(platform, workspace_id, actor="bob") -> None:
    platform.workspaces.add_member(workspace_id, actor, "viewer", "admin")


# ----------------------------------------------------------------------
# constants / boundaries
# ----------------------------------------------------------------------


def test_twin_constants_declare_the_supported_surface():
    assert TWIN_POLICY_KIND == "twin_policy"
    assert TWIN_MODES == ("copy", "scale", "boolean", "delta")
    assert DEFAULT_DEPTH == 1
    assert MAX_DEPTH == 4
    assert MAX_NODES == 500
    assert MAX_SCENARIOS == 20


# ----------------------------------------------------------------------
# snapshot
# ----------------------------------------------------------------------


def test_snapshot_walks_relations_from_the_root(platform, workspace_id):
    result = twin(platform).snapshot(workspace_id, "admin", "Vehicle/V001")

    assert result["workspace_id"] == workspace_id
    assert result["ref"] == "Vehicle/V001"
    assert result["depth"] == 1
    assert result["direction"] == "out"
    assert result["truncated"] is False
    # deterministic BFS: root first, then neighbours in ref order
    assert [node["ref"] for node in result["nodes"]] == [
        "Vehicle/V001", "TollStation/ST001", "TollStation/ST002"]
    root = result["nodes"][0]
    assert root["type"] == "Vehicle"
    assert root["id"] == "V001"
    assert root["properties"] == {"plate": "鲁A12345", "balance": 100}
    assert root["version"] == 1
    assert root["updated_at"]

    assert [(edge["relation"], edge["source"], edge["target"],
             edge["direction"]) for edge in result["edges"]] == [
        ("passes", "Vehicle/V001", "TollStation/ST001", "out"),
        ("passes", "Vehicle/V001", "TollStation/ST002", "out"),
    ]
    assert len(result["stateHash"]) == 64
    assert result["generatedAt"]
    assert result["capabilities"] == {
        "liveState": "kernel-snapshot-at-call",
        "simulation": True,
        "sideEffects": False,
        "processIsolation": False,
    }


def test_snapshot_depth_zero_is_root_only(platform, workspace_id):
    result = twin(platform).snapshot(workspace_id, "admin", "Vehicle/V001",
                                     depth=0)
    assert len(result["nodes"]) == 1
    assert result["edges"] == []
    assert result["nodes"][0]["ref"] == "Vehicle/V001"


def test_snapshot_in_direction_returns_incoming_relations(platform,
                                                          workspace_id):
    result = twin(platform).snapshot(workspace_id, "admin", "TollStation/ST001",
                                     direction="in")
    assert [node["ref"] for node in result["nodes"]] == [
        "TollStation/ST001", "Vehicle/V001", "Vehicle/V002"]
    assert [(edge["relation"], edge["source"], edge["target"],
             edge["direction"]) for edge in result["edges"]] == [
        ("passes", "Vehicle/V001", "TollStation/ST001", "in"),
        ("passes", "Vehicle/V002", "TollStation/ST001", "in"),
    ]


def test_snapshot_state_hash_is_deterministic(platform, workspace_id):
    service = twin(platform)
    first = service.snapshot(workspace_id, "admin", "Vehicle/V001")
    second = service.snapshot(workspace_id, "admin", "Vehicle/V001")
    deeper = service.snapshot(workspace_id, "admin", "Vehicle/V001", depth=0)

    assert first["stateHash"] == second["stateHash"]
    assert first["stateHash"] != deeper["stateHash"]


def test_snapshot_truncates_at_max_nodes(platform, workspace_id):
    result = twin(platform).snapshot(workspace_id, "admin", "Vehicle/V001",
                                     max_nodes=2)
    assert len(result["nodes"]) == 2
    assert result["truncated"] is True


def test_snapshot_unknown_ref_is_not_found(platform, workspace_id):
    with pytest.raises(NotFoundError):
        twin(platform).snapshot(workspace_id, "admin", "Vehicle/NOPE")


@pytest.mark.parametrize("kwargs", [
    {"depth": -1},
    {"depth": MAX_DEPTH + 1},
    {"depth": "1"},
    {"direction": "sideways"},
    {"max_nodes": 0},
    {"max_nodes": MAX_NODES + 1},
])
def test_snapshot_rejects_bad_arguments(platform, workspace_id, kwargs):
    with pytest.raises(ValidationError):
        twin(platform).snapshot(workspace_id, "admin", "Vehicle/V001", **kwargs)


def test_snapshot_requires_viewer_role(platform, workspace_id):
    with pytest.raises(AccessDeniedError):
        twin(platform).snapshot(workspace_id, "stranger", "Vehicle/V001")


# ----------------------------------------------------------------------
# policies
# ----------------------------------------------------------------------


def test_policy_crud_and_name_upsert(platform, workspace_id):
    service = twin(platform)
    created = service.set_policy(
        workspace_id, "admin", "plate-to-name", "Vehicle", "passes", "name",
        source_field="plate", mode="copy")

    assert created["id"]
    assert created["workspace_id"] == workspace_id
    assert created["kind"] == TWIN_POLICY_KIND
    assert created["name"] == "plate-to-name"
    assert created["source_type"] == "Vehicle"
    assert created["relation"] == "passes"
    assert created["target_field"] == "name"
    assert created["source_field"] == "plate"
    assert created["mode"] == "copy"
    assert created["factor"] == 1.0
    assert created["active"] is True
    assert created["version"] == 1
    assert created["created_at"] and created["updated_at"]

    updated = service.set_policy(
        workspace_id, "admin", "plate-to-name", "Vehicle", "passes", "status",
        source_field="balance", mode="boolean", factor=1.0, active=False)

    assert updated["id"] == created["id"]          # upsert, not a new row
    assert updated["target_field"] == "status"
    assert updated["mode"] == "boolean"
    assert updated["active"] is False
    assert updated["version"] == 2
    assert updated["created_at"] == created["created_at"]
    listed = service.list_policies(workspace_id, "admin")
    assert [policy["id"] for policy in listed] == [created["id"]]

    removed = service.delete_policy(workspace_id, "admin", created["id"])
    assert removed == {"deleted": True, "id": created["id"]}
    assert service.list_policies(workspace_id, "admin") == []
    with pytest.raises(NotFoundError):
        service.delete_policy(workspace_id, "admin", created["id"])


@pytest.mark.parametrize("overrides", [
    {"mode": "explode"},
    {"name": "   "},
    {"relation": "ghost"},
    {"source_type": "TollStation"},          # contradicts relation source
    {"target_field": ""},
    {"target_field": "$type"},
    {"factor": "1.0"},
    {"active": "yes"},
])
def test_policy_rejects_invalid_definitions(platform, workspace_id, overrides):
    params = {
        "name": "p", "source_type": "Vehicle", "relation": "passes",
        "target_field": "name", "source_field": "plate", "mode": "copy",
    }
    params.update(overrides)
    with pytest.raises(ValidationError):
        twin(platform).set_policy(workspace_id, "admin", **params)


def test_policy_permissions(platform, workspace_id):
    service = twin(platform)
    created = service.set_policy(
        workspace_id, "admin", "plate-to-name", "Vehicle", "passes", "name",
        source_field="plate")
    add_viewer(platform, workspace_id)

    assert service.list_policies(workspace_id, "bob")
    with pytest.raises(AccessDeniedError):
        service.set_policy(workspace_id, "bob", "x", "Vehicle", "passes",
                           "name", source_field="plate")
    with pytest.raises(AccessDeniedError):
        service.delete_policy(workspace_id, "bob", created["id"])


# ----------------------------------------------------------------------
# simulate: side effects and root changes
# ----------------------------------------------------------------------


def test_simulate_changes_root_without_writing_the_kernel(platform,
                                                          workspace_id):
    service = twin(platform)
    simulation = service.simulate(workspace_id, "admin", "Vehicle/V001",
                                  {"balance": 55})

    assert simulation["ref"] == "Vehicle/V001"
    assert simulation["object_type"] == "Vehicle"
    assert simulation["object_id"] == "V001"
    assert simulation["changes"] == {"balance": 55}
    assert simulation["noSideEffects"] is True
    assert simulation["before"]["violations"] == []
    assert simulation["after"]["violations"] == []
    assert simulation["total_before"] == simulation["total_after"] == 0
    assert simulation["resolved"] == []
    assert simulation["introduced"] == []
    assert simulation["stateHashBefore"] != simulation["stateHashAfter"]

    changed = {item["ref"]: item for item in simulation["changed"]}
    assert changed["Vehicle/V001"]["fields"]["balance"] == {
        "before": 100, "after": 55}

    # the kernel still holds the original value
    stored = gateway(platform, workspace_id).get_object("Vehicle/V001")
    assert stored["properties"]["balance"] == 100


def test_simulate_without_rules_reports_no_rule_delta(platform, workspace_id):
    result = twin(platform).simulate(workspace_id, "admin", "TollStation/ST001",
                                     {"status": "free"})
    assert result["before"]["total"] == 0
    assert result["after"]["total"] == 0
    assert result["changedCount"] == 1


# ----------------------------------------------------------------------
# simulate: rule counterfactuals (real kernel + real rules)
# ----------------------------------------------------------------------


def test_simulate_rule_delta_resolves_and_introduces(platform, workspace_id):
    publish_vehicle_rule(platform, workspace_id)
    service = twin(platform)

    # V002 (balance 20) violates the floor; raising it resolves the violation
    resolved = service.simulate(workspace_id, "admin", "Vehicle/V002",
                                {"balance": 100})
    assert resolved["total_before"] == 1
    assert resolved["total_after"] == 0
    assert resolved["before"]["passed"] is False
    assert resolved["after"]["passed"] is True
    assert len(resolved["resolved"]) == 1
    assert resolved["resolved"][0]["object_id"] == "V002"
    assert resolved["resolved"][0]["severity"] == "error"
    assert resolved["introduced"] == []

    # V001 (balance 100) passes; dropping it to -1 introduces a violation
    introduced = service.simulate(workspace_id, "admin", "Vehicle/V001",
                                  {"balance": -1})
    assert introduced["total_before"] == 0
    assert introduced["total_after"] == 1
    assert introduced["after"]["passed"] is False
    assert len(introduced["introduced"]) == 1
    assert introduced["introduced"][0]["object_id"] == "V001"
    assert introduced["resolved"] == []


def test_simulate_records_audit(platform, workspace_id):
    publish_vehicle_rule(platform, workspace_id)
    twin(platform).simulate(workspace_id, "admin", "Vehicle/V002",
                            {"balance": 60})

    entries = audit_actions(platform, workspace_id, "twin.simulate")
    assert entries
    detail = entries[-1]["detail"]
    assert detail["ref"] == "Vehicle/V002"
    assert detail["changes"] == ["balance"]
    assert detail["resolved"] == 1
    assert detail["introduced"] == 0


# ----------------------------------------------------------------------
# simulate: relation propagation
# ----------------------------------------------------------------------


def test_propagation_writes_targets_in_the_simulation_only(platform,
                                                           workspace_id):
    service = twin(platform)
    service.set_policy(workspace_id, "admin", "plate-to-name", "Vehicle",
                       "passes", "name", source_field="plate", mode="copy")
    service.set_policy(workspace_id, "admin", "balance-to-status", "Vehicle",
                       "passes", "status", source_field="balance",
                       mode="boolean")

    simulation = service.simulate(
        workspace_id, "admin", "Vehicle/V001",
        {"plate": "TEST-PLATE", "balance": 0})

    assert len(simulation["policiesApplied"]) == 4
    changed = {item["ref"]: item for item in simulation["changed"]}
    for station in ("TollStation/ST001", "TollStation/ST002"):
        fields = changed[station]["fields"]
        assert fields["name"] == {"before": "济南西收费站"
                                  if station.endswith("ST001")
                                  else "青岛东收费站",
                                  "after": "TEST-PLATE"}
        assert fields["status"] == {"before": "congested"
                                    if station.endswith("ST001") else "free",
                                    "after": False}
    assert changed["Vehicle/V001"]["fields"]["balance"]["after"] == 0

    # no kernel writes: the stations keep their projected values
    for ref, expected_status in (("TollStation/ST001", "congested"),
                                 ("TollStation/ST002", "free")):
        stored = gateway(platform, workspace_id).get_object(ref)
        assert stored["properties"]["status"] == expected_status
        assert stored["properties"]["name"] in ("济南西收费站", "青岛东收费站")


def test_policy_ids_restrict_propagation(platform, workspace_id):
    service = twin(platform)
    first = service.set_policy(workspace_id, "admin", "plate-to-name",
                               "Vehicle", "passes", "name",
                               source_field="plate")
    second = service.set_policy(workspace_id, "admin", "balance-to-status",
                                "Vehicle", "passes", "status",
                                source_field="balance", mode="boolean")

    simulation = service.simulate(
        workspace_id, "admin", "Vehicle/V001", {"plate": "A", "balance": 0},
        policy_ids=[first["id"]])

    assert {item["policy_id"] for item in simulation["policiesApplied"]} == {
        first["id"]}
    assert second["id"] not in {
        item["policy_id"] for item in simulation["policiesApplied"]}

    # inactive policies never apply, even without a filter
    service.set_policy(workspace_id, "admin", "balance-to-status", "Vehicle",
                       "passes", "status", source_field="balance",
                       mode="boolean", active=False)
    unfiltered = service.simulate(workspace_id, "admin", "Vehicle/V001",
                                  {"plate": "A", "balance": 0})
    assert {item["policy_id"] for item in unfiltered["policiesApplied"]} == {
        first["id"]}


def test_snapshot_is_cycle_safe(platform, workspace_id, monkeypatch):
    """A cyclic relation graph must terminate and not duplicate nodes."""
    ctx = platform.ctx
    real = ctx.gateways.for_workspace(workspace_id,
                                      ctx.default_runtime_config)

    class CyclicGateway:
        def get_object(self, ref):
            return real.get_object(ref)

        def related(self, ref, relation, direction="out"):
            if str(ref).startswith("TollStation/") and direction == "out":
                return ["Vehicle/V001"]      # injected back-edge -> cycle
            return real.related(ref, relation, direction)

    monkeypatch.setattr(
        type(ctx.gateways), "for_workspace",
        lambda self, workspace_id, runtime_config, ring="current":
            CyclicGateway())

    result = twin(platform).snapshot(workspace_id, "admin", "Vehicle/V001",
                                     depth=MAX_DEPTH)

    assert [node["ref"] for node in result["nodes"]] == [
        "Vehicle/V001", "TollStation/ST001", "TollStation/ST002"]
    assert len(result["edges"]) == 4
    assert any(edge["source"] == "TollStation/ST001"
               and edge["target"] == "Vehicle/V001"
               for edge in result["edges"])
    assert result["truncated"] is False


# ----------------------------------------------------------------------
# simulate: validation
# ----------------------------------------------------------------------


@pytest.mark.parametrize("changes", [
    {},
    {"id": "x"},
    {"type": "x"},
    {"properties": {}},
    {"workspace_id": "x"},
    {"tenant_id": "x"},
    {"$type": "x"},
    {"a.b": 1},
    {"": 1},
])
def test_simulate_rejects_invalid_changes(platform, workspace_id, changes):
    with pytest.raises(ValidationError):
        twin(platform).simulate(workspace_id, "admin", "Vehicle/V001", changes)


def test_simulate_unknown_ref_and_bad_depth(platform, workspace_id):
    service = twin(platform)
    with pytest.raises(NotFoundError):
        service.simulate(workspace_id, "admin", "Vehicle/NOPE", {"balance": 1})
    with pytest.raises(ValidationError):
        service.simulate(workspace_id, "admin", "Vehicle/V001",
                         {"balance": 1}, depth=MAX_DEPTH + 1)
    with pytest.raises(ValidationError):
        service.simulate(workspace_id, "admin", "Vehicle/V001",
                         {"balance": 1}, policy_ids="not-a-list")


# ----------------------------------------------------------------------
# scenarios
# ----------------------------------------------------------------------


def test_scenarios_ranks_counterfactuals(platform, workspace_id):
    publish_vehicle_rule(platform, workspace_id)
    result = twin(platform).scenarios(
        workspace_id, "admin", "Vehicle/V002",
        [
            {"name": "raise to 100", "changes": {"balance": 100}},
            {"name": "no change", "changes": {"balance": 20}},
            {"name": "lower to 10", "changes": {"balance": 10}},
            {"name": "drop to -5", "changes": {"balance": -5}},
        ])

    assert result["root"] == "Vehicle/V002"
    assert result["noSideEffects"] is True
    assert [item["name"] for item in result["scenarios"]] == [
        "raise to 100", "no change", "drop to -5", "lower to 10"]
    assert result["best"] == "raise to 100"

    by_name = {item["name"]: item for item in result["scenarios"]}
    raise_best = by_name["raise to 100"]
    assert raise_best["score"] == 1
    assert raise_best["ruleDelta"] == {
        "before": 1, "after": 0, "introduced": 0, "resolved": 1}
    assert raise_best["changedCount"] == 1
    # below the floor the same violation stays: no delta, only a value change
    assert by_name["no change"]["score"] == 0
    assert by_name["no change"]["changedCount"] == 0
    assert by_name["drop to -5"]["score"] == 0
    assert by_name["drop to -5"]["changedCount"] == 1
    assert by_name["drop to -5"]["ruleDelta"] == {
        "before": 1, "after": 1, "introduced": 0, "resolved": 0}


def test_scenarios_requires_well_formed_input(platform, workspace_id):
    service = twin(platform)
    with pytest.raises(ValidationError):
        service.scenarios(workspace_id, "admin", "Vehicle/V001", [])
    with pytest.raises(ValidationError):
        service.scenarios(workspace_id, "admin", "Vehicle/V001",
                          [{"changes": {"balance": 1}}])
    with pytest.raises(ValidationError):
        service.scenarios(workspace_id, "admin", "Vehicle/V001",
                          [{"name": "x", "changes": {}}])
    with pytest.raises(ValidationError):
        service.scenarios(
            workspace_id, "admin", "Vehicle/V001",
            [{"name": f"s{index}", "changes": {"balance": index}}
             for index in range(MAX_SCENARIOS + 1)])


def test_scenarios_are_audited(platform, workspace_id):
    twin(platform).scenarios(
        workspace_id, "admin", "Vehicle/V001",
        [{"name": "a", "changes": {"balance": 10}},
         {"name": "b", "changes": {"balance": 20}}])
    entries = audit_actions(platform, workspace_id, "twin.scenarios")
    assert entries
    detail = entries[-1]["detail"]
    assert detail["ref"] == "Vehicle/V001"
    assert detail["count"] == 2
    assert detail["best"] == "a"


# ----------------------------------------------------------------------
# audit coverage and no-side-effect boundary
# ----------------------------------------------------------------------


def test_all_twin_actions_are_audited(platform, workspace_id):
    service = twin(platform)
    policy = service.set_policy(workspace_id, "admin", "plate-to-name",
                                "Vehicle", "passes", "name",
                                source_field="plate")
    service.snapshot(workspace_id, "admin", "Vehicle/V001")
    service.simulate(workspace_id, "admin", "Vehicle/V001",
                     {"plate": "AUDIT"})
    service.scenarios(workspace_id, "admin", "Vehicle/V001",
                      [{"name": "s", "changes": {"plate": "AUDIT"}}])
    service.delete_policy(workspace_id, "admin", policy["id"])

    for action in ("twin.snapshot", "twin.policy_set", "twin.simulate",
                   "twin.scenarios", "twin.policy_deleted"):
        assert audit_actions(platform, workspace_id, action), action
