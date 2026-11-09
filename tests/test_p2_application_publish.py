"""P2-D: publishing validates interaction surfaces; cloning keeps ui_artifact.

Publishing an application is the promise that every operation surface is
executable: each ``InteractionSpec`` must parse, ids must be unique, and a
capability-backed interaction (``action``/``form``) must point at an existing
*and published* capability.  All problems are reported at once, named by the
interaction, so the console can show the complete list.

Cloning copies the declarative contract, including the generated UI artifact,
into a fresh draft; the source document must stay untouched (deep copy).
"""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError


def _published_capability_id(platform, wid) -> str:
    return platform.capabilities.list(wid, "admin")[0]["id"]


def _draft_capability_id(platform, wid) -> str:
    return platform.capabilities.create(
        wid, "admin", key="p2_pending", name="待发布能力",
        target_type="Vehicle",
        implementation={"kind": "python",
                        "source": "def execute(ctx):\n    return {'success': True}\n"},
    )["id"]


def _app_with_view(platform, wid, name: str = "交互运营台") -> str:
    app = platform.applications.create(wid, "admin", name)
    platform.applications.add_view(
        wid, app["id"], "admin", "vehicles", title="车辆",
        kind="table", object_type="Vehicle", properties=["plate"])
    return app["id"]


def _tamper(platform, wid, app_id, mutate) -> dict:
    doc = platform.applications.get(wid, app_id, "admin")
    mutate(doc)
    platform.ctx.save("application", doc)
    return doc


# ----------------------------------------------------------------------
# happy path
# ----------------------------------------------------------------------


def test_publish_application_with_valid_interactions(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)
    capability_id = _published_capability_id(platform, workspace_id)
    platform.applications.add_interaction(
        workspace_id, app_id, "admin", "recharge_vehicle", "action",
        title="车辆充值", capability_id=capability_id)
    platform.applications.add_interaction(
        workspace_id, app_id, "admin", "dispatch_flow", "workflow",
        title="调度流程")

    published = platform.applications.publish(workspace_id, app_id, "admin")

    assert published["lifecycle"] == "published"
    assert [item["id"] for item in published["interactions"]] == [
        "recharge_vehicle", "dispatch_flow"]
    assert published["interactions"][0]["capability_id"] == capability_id


# ----------------------------------------------------------------------
# invalid interactions block publish and are reported together
# ----------------------------------------------------------------------


def test_publish_application_reports_all_interaction_problems_at_once(
        platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)
    draft_capability = _draft_capability_id(platform, workspace_id)

    def mutate(doc: dict) -> None:
        doc["interactions"] = [
            # unknown operation kind
            {"id": "ghost", "kind": "hologram"},
            # capability-backed without a capability reference
            {"id": "naked_action", "kind": "action"},
            # capability that does not exist
            {"id": "missing_cap", "kind": "action",
             "capability_id": "no-such-capability"},
            # capability exists but is still a draft
            {"id": "draft_cap", "kind": "form",
             "capability_id": draft_capability},
            # duplicate declaration id
            {"id": "dup", "kind": "workflow"},
            {"id": "dup", "kind": "workflow"},
        ]

    _tamper(platform, workspace_id, app_id, mutate)

    with pytest.raises(ValidationError) as excinfo:
        platform.applications.publish(workspace_id, app_id, "admin")

    message = str(excinfo.value)
    assert message.startswith("application validation failed:")
    expected = [
        "unknown kind 'hologram'",
        "interaction 'naked_action': action needs a capability_id",
        "interaction 'missing_cap': capability 'no-such-capability' "
        "does not exist",
        f"interaction 'draft_cap': capability {draft_capability!r} is not "
        "published",
        "interaction 'dup': duplicate interaction id",
    ]
    for fragment in expected:
        assert fragment in message, fragment


def test_publish_application_accepts_valid_interaction_after_repair(
        platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id)
    capability_id = _published_capability_id(platform, workspace_id)
    _tamper(platform, workspace_id, app_id, lambda doc: doc.update(
        interactions=[{"id": "broken", "kind": "action"}]))

    with pytest.raises(ValidationError):
        platform.applications.publish(workspace_id, app_id, "admin")

    _tamper(platform, workspace_id, app_id, lambda doc: doc.update(
        interactions=[{"id": "good", "kind": "action",
                       "capability_id": capability_id}]))
    assert platform.applications.publish(
        workspace_id, app_id, "admin")["lifecycle"] == "published"


# ----------------------------------------------------------------------
# clone copies the declarative contract
# ----------------------------------------------------------------------


def test_clone_copies_ui_artifact_and_interactions(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id, "可复制应用")
    capability_id = _published_capability_id(platform, workspace_id)
    _tamper(platform, workspace_id, app_id, lambda doc: doc.update(
        interactions=[{"id": "recharge", "kind": "action",
                       "capability_id": capability_id}],
        ui_artifact={
            "generation_id": "gen-p2",
            "version": 3,
            "blueprint": {"root": {"kind": "page"}},
        },
    ))
    platform.applications.publish(workspace_id, app_id, "admin")

    source = platform.applications.get(workspace_id, app_id, "admin")
    clone = platform.applications.clone(workspace_id, app_id, "admin")

    assert clone["id"] != app_id
    # a clone is always a fresh draft, even when the source is published
    assert clone["lifecycle"] == "draft"
    assert clone["ui_artifact"] == source["ui_artifact"]
    assert clone["interactions"] == source["interactions"]


def test_clone_ui_artifact_is_a_deep_copy(platform, workspace_id):
    app_id = _app_with_view(platform, workspace_id, "深拷贝应用")
    _tamper(platform, workspace_id, app_id, lambda doc: doc.update(
        ui_artifact={"generation_id": "gen-deep",
                     "blueprint": {"root": {"kind": "page"}}}))

    clone = platform.applications.clone(workspace_id, app_id, "admin")
    clone_doc = platform.applications.get(workspace_id, clone["id"], "admin")
    clone_doc["ui_artifact"]["blueprint"]["root"]["kind"] = "tampered"
    platform.ctx.save("application", clone_doc)

    source = platform.applications.get(workspace_id, app_id, "admin")
    assert source["ui_artifact"]["blueprint"]["root"]["kind"] == "page"
