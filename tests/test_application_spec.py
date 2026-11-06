"""P1 application orchestration contracts: ApplicationSpec / ViewSpec / InteractionSpec.

Pins the "application as one declarative spec" contract (docs/analysis/
ontology-driven-application-architecture.md §3.3/§4.3/§7.3): rich view
surfaces, interaction surfaces (action/workflow/form/agent), advisory
diagnostics and the draft-only interaction editing rules.
"""

from __future__ import annotations

import pytest

from onto_platform.application.application_usecases import (
    AddInteraction,
    GetApplicationSpec,
    RemoveInteraction,
)
from onto_platform.domain.application.model import Application, View
from onto_platform.domain.application.spec import (
    INTERACTION_KINDS,
    VIEW_SURFACES,
    ApplicationSpec,
    InteractionSpec,
    ViewSpec,
)
from onto_platform.errors import NotFoundError, ValidationError


def _app(platform, wid, name="规格应用") -> str:
    return platform.applications.create(wid, "admin", name)["id"]


def _capability_id(platform, wid) -> str:
    return platform.capabilities.list(wid, "admin")[0]["id"]


def _add(platform, wid, app_id, **kwargs) -> dict:
    return AddInteraction(platform.ctx)(wid, app_id, "admin", **kwargs)


def _remove(platform, wid, app_id, interaction_id) -> dict:
    return RemoveInteraction(platform.ctx)(wid, app_id, interaction_id, "admin")


def _get_spec(platform, wid, app_id) -> dict:
    return GetApplicationSpec(platform.ctx)(wid, app_id, "admin")


def _audit_actions(platform, wid) -> list[str]:
    return [entry["action"] for entry in platform.ctx.audit.list(wid, limit=500)]


def _published_app(platform, wid) -> str:
    app_id = _app(platform, wid)
    platform.applications.add_view(
        wid, app_id, "admin", "vehicles", kind="table", object_type="Vehicle")
    return platform.applications.publish(wid, app_id, "admin")["id"]


# ----------------------------------------------------------------------
# ApplicationSpec / ViewSpec
# ----------------------------------------------------------------------


def test_surface_constants_cover_observation_and_operation():
    assert VIEW_SURFACES[:4] == ("table", "card", "chart", "map")
    assert {"graph", "dashboard", "timeline", "spatial"} <= set(VIEW_SURFACES)
    assert INTERACTION_KINDS == ("action", "workflow", "form", "agent")


def test_existing_view_becomes_view_spec(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    platform.applications.add_view(
        workspace_id, app_id, "admin", "vehicles", title="车辆",
        kind="table", object_type="Vehicle", properties=["plate", "balance"])

    result = _get_spec(platform, workspace_id, app_id)

    view = result["spec"]["views"][0]
    assert view["alias"] == "vehicles"
    assert view["title"] == "车辆"
    assert view["kind"] == "table"
    assert view["object_type"] == "Vehicle"
    assert view["properties"] == ["plate", "balance"]
    assert view["query_alias"] == ""
    assert view["config"] == {}
    assert view["layout"] == {}
    assert view["permissions"] == {}
    assert result["valid"] is True
    assert result["diagnostics"] == []

    restored = ApplicationSpec.from_dict(result["spec"])
    assert restored.to_dict() == result["spec"]


def test_view_spec_from_view_round_trips():
    view = View(alias="v", title="车辆", kind="table", object_type="Vehicle",
                properties=["plate"], query_alias="q", config={"a": 1})
    spec = ViewSpec.from_view(view)
    restored = ViewSpec.from_dict(spec.to_dict())
    assert restored.to_dict() == spec.to_dict()
    assert restored.kind == "table"
    assert restored.object_type == "Vehicle"


@pytest.mark.parametrize("kind", ["table", "card", "graph", "dashboard",
                                  "timeline", "spatial"])
def test_view_spec_accepts_observation_surfaces(kind):
    assert ViewSpec(alias="v", kind=kind, object_type="Vehicle").validate() == []


def test_view_spec_chart_and_map_need_render_config():
    assert ViewSpec(alias="c", kind="chart",
                    config={"x": "plate", "y": "balance"}).validate() == []
    assert ViewSpec(alias="c", kind="chart", config={"x": "plate"}).validate()
    assert ViewSpec(alias="m", kind="map",
                    config={"lat": "lat", "lng": "lng"}).validate() == []
    assert ViewSpec(alias="m", kind="map").validate()


def test_view_spec_reports_unknown_surface_and_missing_alias():
    problems = ViewSpec(alias="", kind="hologram").validate()
    assert any("alias" in problem for problem in problems)
    assert any("hologram" in problem for problem in problems)


def test_application_spec_preserves_composition(platform, workspace_id):
    capability_id = _capability_id(platform, workspace_id)
    app_id = _app(platform, workspace_id)
    platform.applications.add_view(
        workspace_id, app_id, "admin", "vehicles", kind="table",
        object_type="Vehicle")
    platform.applications.add_query(
        workspace_id, app_id, "admin", "low_balance", object_type="Vehicle",
        filters={"balance__lt": 50}, expand=["passes"])
    platform.applications.attach_capability(
        workspace_id, app_id, "admin", capability_id)
    platform.applications.add_agent(
        workspace_id, app_id, "admin", "watch", goal="盯住低余额车辆")
    platform.applications.attach_ui_artifact(
        workspace_id, app_id, "admin",
        {"generation_id": "gen-1", "version": 2, "program_hash": "abc"})

    raw = platform.applications.get(workspace_id, app_id, "admin")
    spec = ApplicationSpec.from_application(Application.from_dict(raw))

    assert [query["alias"] for query in spec.queries] == ["low_balance"]
    assert spec.queries[0]["expand"] == [{"relation": "passes",
                                          "direction": "out"}]
    assert spec.capabilities == [capability_id]
    assert spec.agents[0]["alias"] == "watch"
    assert spec.ui_artifact["generation_id"] == "gen-1"
    assert spec.to_dict()["queries"] == spec.queries
    assert ApplicationSpec.from_dict(spec.to_dict()).to_dict() == spec.to_dict()


# ----------------------------------------------------------------------
# InteractionSpec
# ----------------------------------------------------------------------


def test_interaction_spec_requires_capability_for_action_and_form():
    assert InteractionSpec(id="i", kind="action").validate()
    assert InteractionSpec(id="i", kind="form").validate()
    assert InteractionSpec(id="i", kind="action",
                           capability_id="cap-1").validate() == []
    assert InteractionSpec(id="i", kind="form",
                           capability_id="cap-1").validate() == []


def test_interaction_spec_allows_workflow_and_agent_without_capability():
    assert InteractionSpec(id="i", kind="workflow").validate() == []
    assert InteractionSpec(id="i", kind="agent").validate() == []
    assert InteractionSpec(id="i", kind="workflow",
                           config={"steps": []}).validate() == []


def test_interaction_spec_rejects_bad_kind_missing_id_and_bad_config():
    assert any("kind" in problem
               for problem in InteractionSpec(id="i", kind="magic").validate())
    assert any("id" in problem
               for problem in InteractionSpec(
                   id="  ", kind="action", capability_id="cap").validate())
    assert any("config" in problem
               for problem in InteractionSpec(
                   id="i", kind="workflow", config=[]).validate())


def test_interaction_spec_round_trips():
    spec = InteractionSpec(
        id="approve", kind="action", title="审批", capability_id="cap-1",
        config={"mode": "queue"}, permissions={"roles": ["admin"]},
        approval={"required": True})
    restored = InteractionSpec.from_dict(spec.to_dict())
    assert restored.to_dict() == spec.to_dict()


# ----------------------------------------------------------------------
# AddInteraction / RemoveInteraction
# ----------------------------------------------------------------------


def test_add_interaction_on_draft_then_remove(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    capability_id = _capability_id(platform, workspace_id)

    updated = _add(
        platform, workspace_id, app_id, interaction_id="approve",
        kind="action", title="审批", capability_id=capability_id,
        config={"mode": "queue"}, permissions={"roles": ["admin"]},
        approval={"required": True})

    assert updated["interactions"] == [{
        "id": "approve",
        "kind": "action",
        "title": "审批",
        "capability_id": capability_id,
        "config": {"mode": "queue"},
        "permissions": {"roles": ["admin"]},
        "approval": {"required": True},
    }]
    assert "application.add_interaction" in _audit_actions(platform, workspace_id)

    removed = _remove(platform, workspace_id, app_id, "approve")
    assert removed["interactions"] == []
    assert "application.remove_interaction" in _audit_actions(
        platform, workspace_id)

    with pytest.raises(NotFoundError):
        _remove(platform, workspace_id, app_id, "approve")


def test_add_interaction_rejects_duplicates_unknown_capability_and_bad_kind(
        platform, workspace_id):
    app_id = _app(platform, workspace_id)
    capability_id = _capability_id(platform, workspace_id)
    _add(platform, workspace_id, app_id, interaction_id="go", kind="action",
         capability_id=capability_id)

    with pytest.raises(ValidationError, match="already exists"):
        _add(platform, workspace_id, app_id, interaction_id="go",
             kind="action", capability_id=capability_id)
    with pytest.raises(NotFoundError):
        _add(platform, workspace_id, app_id, interaction_id="other",
             kind="action", capability_id="missing-capability")
    with pytest.raises(ValidationError, match="capability_id"):
        _add(platform, workspace_id, app_id, interaction_id="nope",
             kind="action")
    with pytest.raises(ValidationError):
        _add(platform, workspace_id, app_id, interaction_id="bad",
             kind="magic")


def test_add_workflow_interaction_without_capability(platform, workspace_id):
    app_id = _app(platform, workspace_id)

    updated = _add(platform, workspace_id, app_id, interaction_id="review",
                   kind="workflow", config={"steps": [{"kind": "find",
                                                       "object_type": "Vehicle"}]})

    assert updated["interactions"][0]["kind"] == "workflow"
    assert updated["interactions"][0]["capability_id"] == ""
    assert _get_spec(platform, workspace_id, app_id)["valid"] is True


def test_published_application_interactions_are_immutable(platform, workspace_id):
    app_id = _published_app(platform, workspace_id)
    capability_id = _capability_id(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        _add(platform, workspace_id, app_id, interaction_id="after_publish",
             kind="action", capability_id=capability_id)
    assert "clone" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        _remove(platform, workspace_id, app_id, "after_publish")
    assert "clone" in str(excinfo.value)


def test_clone_application_copies_interactions(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    capability_id = _capability_id(platform, workspace_id)
    _add(platform, workspace_id, app_id, interaction_id="approve",
         kind="action", capability_id=capability_id, config={"mode": "queue"})

    clone = platform.applications.clone(workspace_id, app_id, "admin")

    source = platform.applications.get(workspace_id, app_id, "admin")
    assert clone["interactions"] == source["interactions"]
    assert clone["interactions"][0]["id"] == "approve"
    assert clone["lifecycle"] == "draft"
    # the clone is a separate draft: editing it does not touch the source
    _remove(platform, workspace_id, clone["id"], "approve")
    assert platform.applications.get(
        workspace_id, app_id, "admin")["interactions"][0]["id"] == "approve"


# ----------------------------------------------------------------------
# GetApplicationSpec diagnostics
# ----------------------------------------------------------------------


def test_get_application_spec_surfaces_dangling_references(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    raw = platform.applications.get(workspace_id, app_id, "admin")
    raw["views"]["ghost"] = {
        "alias": "ghost", "title": "幽灵", "kind": "table",
        "object_type": "GhostType", "properties": [], "query_alias": "missing",
        "config": {},
    }
    platform.ctx.save("application", raw)

    result = _get_spec(platform, workspace_id, app_id)

    codes = {problem["code"] for problem in result["diagnostics"]}
    assert {"unknown_object_type", "unknown_query"} <= codes
    assert result["valid"] is False
    assert all({"code", "message", "path"} <= set(problem)
               for problem in result["diagnostics"])


def test_diagnostics_reports_view_config_and_capability_problems():
    spec = ApplicationSpec(
        application_id="a", workspace_id="w", name="坏应用",
        views=[ViewSpec(alias="c", kind="chart")],
        interactions=[InteractionSpec(id="i", kind="action",
                                      capability_id="gone")])
    problems = spec.diagnostics({"objects": {"Vehicle": {}}}, {"cap-1"})
    codes = {problem["code"] for problem in problems}
    assert {"missing_view_config", "unknown_capability"} <= codes
    # omitting capability_ids skips the capability existence check
    without = {problem["code"] for problem in
               spec.diagnostics({"objects": {"Vehicle": {}}}, None)}
    assert "unknown_capability" not in without


def test_diagnostics_reports_missing_ontology():
    spec = ApplicationSpec(
        application_id="a", workspace_id="w", name="空",
        views=[ViewSpec(alias="v", kind="table", object_type="Vehicle")])
    problems = spec.diagnostics(None)
    assert [problem["code"] for problem in problems] == ["ontology_missing"]


# ----------------------------------------------------------------------
# backward compatibility
# ----------------------------------------------------------------------


def test_legacy_application_data_loads_with_empty_interactions():
    legacy = {
        "id": "app-1", "workspace_id": "w-1", "name": "旧应用",
        "description": "", "views": {}, "queries": {}, "capabilities": [],
        "agents": [],
    }

    app = Application.from_dict(legacy)
    payload = app.to_dict()

    assert app.interactions == []
    assert payload["interactions"] == []
    assert Application.from_dict(payload).interactions == []


def test_new_application_always_serialises_interactions(platform, workspace_id):
    app_id = _app(platform, workspace_id)
    raw = platform.applications.get(workspace_id, app_id, "admin")
    assert raw["interactions"] == []
