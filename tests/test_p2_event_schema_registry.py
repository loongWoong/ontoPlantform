"""P2.6: event schema registry — versions, validation and compatibility."""

from __future__ import annotations

import pytest

from onto_platform.application.event_schema_registry import (
    COMPATIBILITY_MODES,
    EVENT_SCHEMA_KIND,
    SchemaRegistry,
    breaking_changes,
)
from onto_platform.errors import AccessDeniedError, NotFoundError, ValidationError

BASE_SCHEMA = {
    "vid": {"type": "string", "required": True},
    "speed": {"type": "number"},
}


def _registry(platform) -> SchemaRegistry:
    return SchemaRegistry(platform.ctx)


def _audit_actions(platform, workspace_id: str) -> list[str]:
    return [entry["action"] for entry in platform.ctx.audit.list(workspace_id)]


# ----------------------------------------------------------------------
# registration / reads
# ----------------------------------------------------------------------


def test_register_list_and_get_versions(platform, workspace_id):
    registry = _registry(platform)
    v1 = registry.register(
        workspace_id, "admin", "traffic.vehicle_entered", 1, BASE_SCHEMA,
        description="vehicle entered a zone")

    assert v1["id"] == "es_traffic.vehicle_entered_v1"
    assert v1["workspace_id"] == workspace_id
    assert v1["event_type"] == "traffic.vehicle_entered"
    assert v1["version"] == 1
    assert v1["compatibility"] == "backward"
    assert v1["description"] == "vehicle entered a zone"
    assert v1["created_by"] == "admin"
    assert v1["created_at"]
    # The domain normalizer fills required/description deterministically.
    assert v1["payload_schema"]["vid"] == {
        "type": "string", "required": True, "description": ""}
    assert v1["payload_schema"]["speed"]["required"] is False

    v2 = registry.register(
        workspace_id, "admin", "traffic.vehicle_entered", 2,
        {**BASE_SCHEMA, "fuel": {"type": "number"}})

    listed = registry.list(
        workspace_id, "admin", "traffic.vehicle_entered")
    assert [row["version"] for row in listed] == [1, 2]
    assert registry.get(workspace_id, "admin",
                        "traffic.vehicle_entered")["version"] == 2
    assert registry.get(workspace_id, "admin",
                        "traffic.vehicle_entered", 1)["version"] == 1
    assert [row["version"] for row in registry.list(workspace_id, "admin")] \
        == [1, 2]
    assert v2["payload_schema"]["fuel"]["required"] is False

    with pytest.raises(NotFoundError):
        registry.get(workspace_id, "admin", "traffic.vehicle_entered", 9)
    with pytest.raises(NotFoundError):
        registry.get(workspace_id, "admin", "traffic.other_event")

    # Versions are immutable: a duplicate (event_type, version) is rejected.
    with pytest.raises(ValidationError, match="already"):
        registry.register(
            workspace_id, "admin", "traffic.vehicle_entered", 1, BASE_SCHEMA)

    actions = _audit_actions(platform, workspace_id)
    assert "event_schema.registered" in actions


@pytest.mark.parametrize("event_type", [
    "Vehicle", "traffic", "Traffic.entered", "traffic..x", "traffic."])
def test_register_rejects_invalid_event_names(platform, workspace_id,
                                              event_type):
    with pytest.raises(ValidationError):
        _registry(platform).register(
            workspace_id, "admin", event_type, 1, BASE_SCHEMA)


@pytest.mark.parametrize("version", [0, -1, "1", True, 1.5, None])
def test_register_rejects_invalid_versions(platform, workspace_id, version):
    with pytest.raises(ValidationError):
        _registry(platform).register(
            workspace_id, "admin", "traffic.vehicle_entered", version,
            BASE_SCHEMA)


@pytest.mark.parametrize("schema", [
    "not-a-dict",
    {"vid": "string"},
    {"vid": {"type": "date"}},
    {"vid": {"type": "string", "required": "yes"}},
    {"": {"type": "string"}},
])
def test_register_rejects_invalid_payload_schemas(platform, workspace_id,
                                                  schema):
    with pytest.raises(ValidationError):
        _registry(platform).register(
            workspace_id, "admin", "traffic.vehicle_entered", 1, schema)


def test_register_rejects_invalid_compatibility_mode(platform, workspace_id):
    with pytest.raises(ValidationError):
        _registry(platform).register(
            workspace_id, "admin", "traffic.vehicle_entered", 1, BASE_SCHEMA,
            compatibility="strict")
    assert COMPATIBILITY_MODES == ("backward", "forward", "full", "none")


# ----------------------------------------------------------------------
# compatibility rules
# ----------------------------------------------------------------------


def test_backward_compatible_addition_is_allowed(platform, workspace_id):
    registry = _registry(platform)
    registry.register(workspace_id, "admin", "traffic.vehicle_entered", 1,
                      BASE_SCHEMA)
    v2 = registry.register(
        workspace_id, "admin", "traffic.vehicle_entered", 2,
        {**BASE_SCHEMA, "fuel": {"type": "number"}})
    assert v2["version"] == 2


@pytest.mark.parametrize("candidate,fragment", [
    ({"vid": {"type": "string", "required": True}}, "speed"),
    ({"vid": {"type": "number", "required": True},
      "speed": {"type": "number"}}, "vid"),
    ({"vid": {"type": "string", "required": True},
      "speed": {"type": "number", "required": True}}, "speed"),
    ({**BASE_SCHEMA, "fuel": {"type": "number", "required": True}}, "fuel"),
])
def test_backward_breaking_changes_are_rejected(platform, workspace_id,
                                                candidate, fragment):
    registry = _registry(platform)
    registry.register(workspace_id, "admin", "traffic.vehicle_entered", 1,
                      BASE_SCHEMA)
    with pytest.raises(ValidationError, match="not backward-compatible") \
            as excinfo:
        registry.register(workspace_id, "admin", "traffic.vehicle_entered", 2,
                          candidate)
    assert fragment in str(excinfo.value)


def test_compatibility_dry_run_does_not_write(platform, workspace_id):
    registry = _registry(platform)
    registry.register(workspace_id, "admin", "traffic.vehicle_entered", 1,
                      BASE_SCHEMA)

    removed = registry.compatibility(
        workspace_id, "admin", "traffic.vehicle_entered",
        {"vid": {"type": "string", "required": True}})
    assert removed["compatible"] is False
    assert removed["mode"] == "backward"
    assert removed["removed"] == ["speed"]
    assert removed["added"] == []
    assert removed["typeChanged"] == []
    assert removed["breaking"] == [{"field": "speed",
                                   "reason": "removed_field"}]

    type_changed = registry.compatibility(
        workspace_id, "admin", "traffic.vehicle_entered",
        {"vid": {"type": "number", "required": True},
         "speed": {"type": "number"}})
    assert type_changed["typeChanged"] == ["vid"]
    assert {"field": "vid", "reason": "type_changed"} \
        in type_changed["breaking"]

    added = registry.compatibility(
        workspace_id, "admin", "traffic.vehicle_entered",
        {**BASE_SCHEMA, "fuel": {"type": "number"}})
    assert added["compatible"] is True
    assert added["added"] == ["fuel"]
    assert added["breaking"] == []

    # Nothing was persisted by the dry-runs.
    assert len(registry.list(workspace_id, "admin")) == 1
    actions = _audit_actions(platform, workspace_id)
    assert "event_schema.checked" in actions


def test_compatibility_can_pin_a_from_version(platform, workspace_id):
    registry = _registry(platform)
    registry.register(workspace_id, "admin", "traffic.vehicle_entered", 1,
                      BASE_SCHEMA)
    registry.register(workspace_id, "admin", "traffic.vehicle_entered", 2,
                      {**BASE_SCHEMA, "fuel": {"type": "number"}})

    from_v1 = registry.compatibility(
        workspace_id, "admin", "traffic.vehicle_entered",
        {**BASE_SCHEMA, "fuel": {"type": "number"},
         "extra": {"type": "string"}}, from_version=1)
    assert from_v1["compatible"] is True
    assert from_v1["added"] == ["extra", "fuel"]

    from_v2 = registry.compatibility(
        workspace_id, "admin", "traffic.vehicle_entered",
        {**BASE_SCHEMA, "fuel": {"type": "number"},
         "extra": {"type": "string"}}, from_version=2)
    assert from_v2["added"] == ["extra"]

    with pytest.raises(NotFoundError):
        registry.compatibility(
            workspace_id, "admin", "traffic.vehicle_entered", BASE_SCHEMA,
            from_version=9)


def test_none_mode_skips_compatibility_check(platform, workspace_id):
    registry = _registry(platform)
    registry.register(workspace_id, "admin", "traffic.vehicle_entered", 1,
                      BASE_SCHEMA)
    v2 = registry.register(
        workspace_id, "admin", "traffic.vehicle_entered", 2,
        {"vid": {"type": "string", "required": True}},
        compatibility="none")
    assert v2["compatibility"] == "none"


def test_forward_and_full_modes_are_strict(platform, workspace_id):
    registry = _registry(platform)
    registry.register(workspace_id, "admin", "traffic.forward_event", 1,
                      BASE_SCHEMA)
    with pytest.raises(ValidationError, match="not forward-compatible"):
        registry.register(
            workspace_id, "admin", "traffic.forward_event", 2,
            {**BASE_SCHEMA, "fuel": {"type": "number"}},
            compatibility="forward")
    assert registry.register(
        workspace_id, "admin", "traffic.forward_event", 2, BASE_SCHEMA,
        compatibility="forward")["version"] == 2

    registry.register(workspace_id, "admin", "traffic.full_event", 1,
                      BASE_SCHEMA)
    with pytest.raises(ValidationError, match="not full-compatible"):
        registry.register(
            workspace_id, "admin", "traffic.full_event", 2,
            {**BASE_SCHEMA, "fuel": {"type": "number"}},
            compatibility="full")


def test_breaking_changes_helper_is_pure_and_mode_aware():
    previous = {
        "vid": {"type": "string", "required": True},
        "speed": {"type": "number", "required": False},
    }
    candidate = {
        "vid": {"type": "string", "required": True},
        "speed": {"type": "number", "required": False},
        "fuel": {"type": "number", "required": False},
    }
    assert breaking_changes(previous, previous) == []
    assert breaking_changes(previous, candidate) == []
    assert breaking_changes(previous, candidate, "forward") == [
        {"field": "fuel", "reason": "added_field"}]
    assert breaking_changes(previous, candidate, "none") == []
    # The helper does not mutate its inputs.
    assert "fuel" not in previous


# ----------------------------------------------------------------------
# authorization
# ----------------------------------------------------------------------


def test_registry_authorization(platform, workspace_id):
    registry = _registry(platform)
    platform.workspaces.add_member(workspace_id, "bob", "viewer",
                                   actor="admin")
    platform.workspaces.add_member(workspace_id, "carol", "editor",
                                   actor="admin")

    with pytest.raises(AccessDeniedError):
        registry.register(workspace_id, "bob", "traffic.vehicle_entered", 1,
                          BASE_SCHEMA)
    with pytest.raises(AccessDeniedError):
        registry.list(workspace_id, "outsider")

    row = registry.register(workspace_id, "carol", "traffic.vehicle_entered",
                            1, BASE_SCHEMA)
    assert row["created_by"] == "carol"
    # viewers may read schemas
    assert [item["id"] for item in registry.list(workspace_id, "bob")] == \
        [row["id"]]
    assert registry.get(workspace_id, "bob",
                        "traffic.vehicle_entered")["version"] == 1
    assert EVENT_SCHEMA_KIND == "event_schema"
