"""Application publish validation (task-16).

Publishing an application is the promise that its views / queries /
capabilities resolve against the *published* ontology.  These tests pin the
whole reference check and the "all problems at once" contract the console
relies on when it shows the failure list.
"""

from __future__ import annotations

import pytest

from onto_platform.errors import AccessDeniedError, LifecycleError, ValidationError
from tests.conftest import http_post


def _published_capability_id(platform, wid) -> str:
    return platform.capabilities.list(wid, "admin")[0]["id"]


def _app_with_valid_view(platform, wid) -> str:
    app = platform.applications.create(wid, "admin", "运营台")
    platform.applications.add_view(
        wid, app["id"], "admin", "vehicles", title="车辆", kind="table",
        object_type="Vehicle", properties=["plate", "balance"])
    return app["id"]


def _audit_actions(platform, wid) -> list[str]:
    return [entry["action"] for entry in platform.ctx.audit.list(wid, limit=500)]


# ----------------------------------------------------------------------
# happy path
# ----------------------------------------------------------------------


def test_publish_application_with_valid_references(platform, workspace_id):
    app_id = _app_with_valid_view(platform, workspace_id)
    platform.applications.add_query(
        workspace_id, app_id, "admin", "low_balance",
        object_type="Vehicle", filters={"balance__lt": 50},
        expand=["passes"])
    platform.applications.attach_capability(
        workspace_id, app_id, "admin",
        _published_capability_id(platform, workspace_id))

    published = platform.applications.publish(workspace_id, app_id, "admin")

    assert published["lifecycle"] == "published"
    assert published["views"]["vehicles"]["object_type"] == "Vehicle"
    assert published["queries"]["low_balance"]["expand"] == [
        {"relation": "passes", "direction": "out"}]
    assert "application.publish" in _audit_actions(platform, workspace_id)


def test_publish_application_requires_a_view_or_query(platform, workspace_id):
    app = platform.applications.create(workspace_id, "admin", "空应用")

    with pytest.raises(ValidationError) as excinfo:
        platform.applications.publish(workspace_id, app["id"], "admin")

    assert "at least one view or query" in str(excinfo.value)


# ----------------------------------------------------------------------
# ontology contract
# ----------------------------------------------------------------------


def test_publish_application_requires_published_ontology(platform):
    wid = platform.workspaces.create("草稿本体空间", actor="admin")["id"]
    platform.ontologies.create(wid, "草稿本体", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"}])
    app = platform.applications.create(wid, "admin", "草稿应用")
    platform.applications.add_view(
        wid, app["id"], "admin", "vehicles", object_type="Vehicle",
        properties=["plate"])

    with pytest.raises(ValidationError) as excinfo:
        platform.applications.publish(wid, app["id"], "admin")

    message = str(excinfo.value)
    assert "no published ontology revision" in message
    assert "publish the ontology first" in message


def test_application_publish_unblocked_after_ontology_republished(platform):
    wid = platform.workspaces.create("修订本体空间", actor="admin")["id"]
    platform.ontologies.create(wid, "修订本体", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"}])
    platform.ontologies.publish(wid, "admin")
    app = platform.applications.create(wid, "admin", "修订应用")
    platform.applications.add_view(
        wid, app["id"], "admin", "vehicles", object_type="Vehicle",
        properties=["plate"])
    assert platform.applications.publish(
        wid, app["id"], "admin")["lifecycle"] == "published"

    # revising the ontology removes the published contract until the revision
    # is published: a *new* app cannot be published against only a draft
    platform.ontologies.revise(wid, "admin")
    other = platform.applications.create(wid, "admin", "第二应用")
    platform.applications.add_view(
        wid, other["id"], "admin", "vehicles", object_type="Vehicle",
        properties=["plate"])
    with pytest.raises(ValidationError):
        platform.applications.publish(wid, other["id"], "admin")

    platform.ontologies.publish(wid, "admin")
    assert platform.applications.publish(
        wid, other["id"], "admin")["lifecycle"] == "published"


def test_check_object_type_uses_active_revision_not_row_order(platform, workspace_id):
    # the published row is first in storage; the active revision is the draft.
    # AddView/AddQuery must validate against the draft, never against row[0].
    platform.ontologies.revise(workspace_id, "admin")
    platform.ontologies.upsert_object(
        workspace_id, "admin", "ChargingStation",
        properties=[{"name": "name", "type": "string"}])
    app = platform.applications.create(workspace_id, "admin", "充电应用")

    updated = platform.applications.add_view(
        workspace_id, app["id"], "admin", "stations",
        object_type="ChargingStation", properties=["name"])
    assert "stations" in updated["views"]

    updated = platform.applications.add_query(
        workspace_id, app["id"], "admin", "stations_query",
        object_type="ChargingStation", filters={"name__eq": "A"})
    assert updated["queries"]["stations_query"]["object_type"] == "ChargingStation"

    with pytest.raises(ValidationError):
        platform.applications.add_view(
            workspace_id, app["id"], "admin", "ghost",
            object_type="Ghost", properties=[])


# ----------------------------------------------------------------------
# reference validation
# ----------------------------------------------------------------------


def test_publish_application_reports_all_problems_at_once(platform, workspace_id):
    app_id = _app_with_valid_view(platform, workspace_id)
    platform.applications.add_query(
        workspace_id, app_id, "admin", "low_balance",
        object_type="Vehicle", filters={"balance__lt": 50})
    draft_capability = platform.capabilities.create(
        workspace_id, "admin", key="draft_cap", name="草稿能力",
        target_type="Vehicle",
        implementation={"kind": "python",
                        "source": "def execute(ctx):\n    return {'success': True}\n"})

    # tamper the stored document so every independent rule fails at once
    doc = platform.applications.get(workspace_id, app_id, "admin")
    doc["views"]["vehicles"]["kind"] = "3d"
    doc["views"]["vehicles"]["properties"] = ["plate", "missing_prop"]
    doc["views"]["vehicles"]["query_alias"] = "ghost_query"
    doc["views"]["bad"] = {
        "alias": "bad", "title": "坏视图", "kind": "table",
        "object_type": "Alien", "properties": [], "query_alias": ""}
    doc["queries"]["low_balance"]["filters"] = {"missing_field__gte": 1}
    doc["queries"]["low_balance"]["expand"] = [
        {"relation": "ghost_rel", "direction": "sideways"}]
    doc["capabilities"] = [
        _published_capability_id(platform, workspace_id),
        draft_capability["id"],
        "missing-cap",
    ]
    doc["agents"] = [
        {"alias": "assistant", "goal": ""},
        {"alias": "assistant", "goal": "检查拥堵"},
        {"alias": "", "goal": " "},
    ]
    platform.ctx.save("application", doc)

    with pytest.raises(ValidationError) as excinfo:
        platform.applications.publish(workspace_id, app_id, "admin")

    message = str(excinfo.value)
    assert message.startswith("application validation failed:")
    expected = [
        "unknown kind '3d'",
        "unknown property 'missing_prop'",
        "query_alias 'ghost_query' does not name a query",
        "unknown object type 'Alien'",
        "unknown filter property 'missing_field__gte'",
        "unknown relation 'ghost_rel'",
        "direction 'sideways' must be 'out' or 'in'",
        "attached capability 'missing-cap' does not exist",
        f"attached capability {draft_capability['id']!r} is not published",
        "duplicate application agent alias 'assistant'",
        "goal must not be empty",
        "agent needs a non-empty alias",
    ]
    for fragment in expected:
        assert fragment in message, fragment


def test_publish_application_rejects_draft_capability(platform, workspace_id):
    app_id = _app_with_valid_view(platform, workspace_id)
    draft = platform.capabilities.create(
        workspace_id, "admin", key="pending", name="待发布能力",
        target_type="Vehicle",
        implementation={"kind": "python",
                        "source": "def execute(ctx):\n    return {'success': True}\n"})
    doc = platform.applications.get(workspace_id, app_id, "admin")
    doc["capabilities"] = [draft["id"]]
    platform.ctx.save("application", doc)

    with pytest.raises(ValidationError) as excinfo:
        platform.applications.publish(workspace_id, app_id, "admin")

    message = str(excinfo.value)
    assert "is not published" in message
    assert "lifecycle 'draft'" in message


# ----------------------------------------------------------------------
# governance / lifecycle
# ----------------------------------------------------------------------


def test_publish_application_requires_editor(platform, workspace_id):
    app_id = _app_with_valid_view(platform, workspace_id)
    platform.workspaces.add_member(workspace_id, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.applications.publish(workspace_id, app_id, "bob")


def test_published_application_cannot_be_republished(platform, workspace_id):
    app_id = _app_with_valid_view(platform, workspace_id)
    assert platform.applications.publish(
        workspace_id, app_id, "admin")["lifecycle"] == "published"

    with pytest.raises(LifecycleError):
        platform.applications.publish(workspace_id, app_id, "admin")


# ----------------------------------------------------------------------
# HTTP contract
# ----------------------------------------------------------------------


def test_publish_application_over_http(http_server, platform, workspace_id):
    base, wid = http_server
    status, app = http_post(
        base, f"/api/workspaces/{wid}/applications",
        {"user": "admin", "name": "HTTP 运营台"})
    assert status == 200
    aid = app["id"]
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/views",
        {"user": "admin", "alias": "vehicles", "title": "车辆",
         "kind": "table", "object_type": "Vehicle",
         "properties": ["plate", "balance"]})
    assert status == 200
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/queries",
        {"user": "admin", "alias": "low", "object_type": "Vehicle",
         "filters": {"balance__lt": 100}})
    assert status == 200
    capability_id = _published_capability_id(platform, wid)
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/capabilities",
        {"user": "admin", "capability_id": capability_id})
    assert status == 200

    # viewer cannot publish
    http_post(base, f"/api/workspaces/{wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})
    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/publish",
        {"user": "bob"})
    assert status == 403

    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/publish",
        {"user": "admin"})
    assert status == 200
    assert body["lifecycle"] == "published"

    # republishing a published application is rejected
    status, _ = http_post(
        base, f"/api/workspaces/{wid}/applications/{aid}/publish",
        {"user": "admin"})
    assert status == 409

    # a broken application reports the complete problem list as HTTP 400
    status, broken = http_post(
        base, f"/api/workspaces/{wid}/applications",
        {"user": "admin", "name": "坏应用"})
    assert status == 200
    status, body = http_post(
        base, f"/api/workspaces/{wid}/applications/{broken['id']}/publish",
        {"user": "admin"})
    assert status == 400
    assert "application validation failed" in body["error"]
