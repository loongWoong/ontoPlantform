"""Capability draft editing and cloning (task-16).

A capability is the executable extension point users evolve over time.  The
contract is: only drafts may be edited in place, published ones are evolved by
cloning them into a new draft (so the serving runtime never changes under the
publish pointer), and ``{target_type}.{key}`` stays unique because it is the
kernel function name.
"""

from __future__ import annotations

import pytest

from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)
from tests.conftest import http_call, http_post

SOURCE_V1 = (
    "def execute(ctx):\n"
    "    return {'success': True, 'version': 1}\n"
)
SOURCE_V2 = (
    "def execute(ctx):\n"
    "    return {'success': True, 'version': 2}\n"
)


def _create_draft(platform, wid, key="tune", name="调整能力",
                  target_type="Vehicle", **overrides):
    implementation = overrides.pop(
        "implementation", {"kind": "python", "source": SOURCE_V1})
    contract = overrides.pop("contract", {"effects": ["balance"]})
    return platform.capabilities.create(
        wid, "admin", key=key, name=name, target_type=target_type,
        implementation=implementation, contract=contract, **overrides)


def _published_recharge(platform, wid) -> dict:
    for row in platform.capabilities.list(wid, "admin"):
        if row["key"] == "recharge":
            return row
    raise AssertionError("seeded capability missing")


def _audit(platform, wid, action):
    return [entry for entry in platform.ctx.audit.list(wid, limit=500)
            if entry["action"] == action]


# ----------------------------------------------------------------------
# update
# ----------------------------------------------------------------------


def test_update_draft_capability_fields_and_audit(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)

    updated = platform.capabilities.update(
        workspace_id, "admin", capability["id"],
        name="  调整 v2  ", description="新的说明",
        target_type="TollStation",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": SOURCE_V2},
        contract={"effects": ["status"], "idempotent": True})

    assert updated["id"] == capability["id"]
    assert updated["key"] == "tune"                     # untouched
    assert updated["name"] == "调整 v2"                 # trimmed
    assert updated["description"] == "新的说明"
    assert updated["target_type"] == "TollStation"
    assert f"{updated['target_type']}.{updated['key']}" == "TollStation.tune"
    assert updated["inputs"] == [{
        "name": "amount", "type": "number", "description": ""}]
    assert updated["implementation"]["source"] == SOURCE_V2
    assert updated["implementation"]["entrypoint"] is None   # not published yet
    assert updated["contract"] == {"effects": ["status"], "idempotent": True}
    assert updated["lifecycle"] == "draft"

    stored = [row for row in platform.capabilities.list(workspace_id, "admin")
              if row["id"] == capability["id"]][0]
    assert stored["implementation"]["source"] == SOURCE_V2
    entries = _audit(platform, workspace_id, "capability.update")
    assert entries and entries[-1]["detail"]["function"] == "TollStation.tune"


def test_update_draft_capability_can_rename_key(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)

    updated = platform.capabilities.update(
        workspace_id, "admin", capability["id"], key="tune_v2")

    assert updated["key"] == "tune_v2"
    assert f"{updated['target_type']}.{updated['key']}" == "Vehicle.tune_v2"


def test_update_rejects_published_capability(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.update(
            workspace_id, "admin", source["id"], name="改名")

    message = str(excinfo.value)
    assert "only a draft capability can be edited" in message
    assert "clone it" in message and "delete it" in message


def test_update_rejects_validated_capability(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)
    doc = platform.ctx.repo.get(
        "capability", workspace_id, capability["id"])
    doc["lifecycle"] = "validated"
    platform.ctx.save("capability", doc)

    with pytest.raises(ValidationError):
        platform.capabilities.update(
            workspace_id, "admin", capability["id"], name="改名")


def test_update_requires_editor(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)
    platform.workspaces.add_member(workspace_id, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.capabilities.update(
            workspace_id, "bob", capability["id"], name="改名")


def test_update_requires_at_least_one_field(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.update(workspace_id, "admin", capability["id"])

    assert "nothing to update" in str(excinfo.value)


def test_update_unknown_target_type(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.update(
            workspace_id, "admin", capability["id"], target_type="Alien")

    assert "not defined in the ontology" in str(excinfo.value)


def test_update_duplicate_function_name(platform, workspace_id):
    first = _create_draft(platform, workspace_id, key="first", name="一号")
    _create_draft(platform, workspace_id, key="second", name="二号")

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.update(
            workspace_id, "admin", first["id"], key="second")

    message = str(excinfo.value)
    assert "Vehicle.second" in message
    assert "already exists" in message


def test_update_revalidates_implementation(platform, workspace_id):
    capability = _create_draft(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.update(
            workspace_id, "admin", capability["id"],
            implementation={"kind": "python"})       # no source / entrypoint

    message = str(excinfo.value)
    assert "capability validation failed" in message
    assert "needs 'source' or 'entrypoint'" in message


# ----------------------------------------------------------------------
# clone
# ----------------------------------------------------------------------


def test_clone_published_capability_into_draft(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)

    clone = platform.capabilities.clone(
        workspace_id, "admin", source["id"])

    assert clone["id"] != source["id"]
    assert clone["key"] == "recharge_v2"
    assert clone["name"] == "车辆充值 副本"
    assert clone["target_type"] == source["target_type"]
    assert clone["lifecycle"] == "draft"
    assert clone["implementation"]["source"] == source["implementation"]["source"]
    assert clone["contract"] == source["contract"]
    assert clone["inputs"] == source["inputs"]
    assert clone["created_at"]

    # the source keeps serving untouched
    stored = {row["id"]: row
              for row in platform.capabilities.list(workspace_id, "admin")}
    assert stored[source["id"]]["lifecycle"] == "published"
    assert stored[source["id"]]["key"] == "recharge"

    entries = _audit(platform, workspace_id, "capability.clone")
    assert entries
    assert entries[-1]["detail"]["source_id"] == source["id"]


def test_clone_with_custom_key_and_name(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)

    clone = platform.capabilities.clone(
        workspace_id, "admin", source["id"], key="recharge_fast",
        name="快速充值")

    assert clone["key"] == "recharge_fast"
    assert clone["name"] == "快速充值"
    assert f"{clone['target_type']}.{clone['key']}" == "Vehicle.recharge_fast"
    assert clone["lifecycle"] == "draft"


def test_clone_rejects_existing_function_name(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.clone(
            workspace_id, "admin", source["id"], key="recharge")

    message = str(excinfo.value)
    assert "already exists" in message
    assert "recharge" in message


def test_clone_rejects_invalid_identifier(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)

    with pytest.raises(ValidationError) as excinfo:
        platform.capabilities.clone(
            workspace_id, "admin", source["id"], key="not a key")

    assert "not an identifier" in str(excinfo.value)


def test_clone_requires_editor(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)
    platform.workspaces.add_member(workspace_id, "bob", "viewer", "admin")

    with pytest.raises(AccessDeniedError):
        platform.capabilities.clone(workspace_id, "bob", source["id"])


def test_clone_missing_source_is_not_found(platform, workspace_id):
    with pytest.raises(NotFoundError):
        platform.capabilities.clone(workspace_id, "admin", "no-such-capability")


def test_publishing_clone_keeps_source_published(platform, workspace_id):
    source = _published_recharge(platform, workspace_id)
    clone = platform.capabilities.clone(workspace_id, "admin", source["id"])

    result = platform.capabilities.publish(workspace_id, clone["id"], "admin")

    assert result["capability"]["lifecycle"] == "published"
    assert result["capability"]["key"] == "recharge_v2"
    stored = {row["key"]: row
              for row in platform.capabilities.list(workspace_id, "admin")}
    assert stored["recharge"]["lifecycle"] == "published"
    assert stored["recharge_v2"]["lifecycle"] == "published"


# ----------------------------------------------------------------------
# HTTP contract
# ----------------------------------------------------------------------


def test_capability_patch_and_clone_over_http(http_server, platform,
                                              workspace_id):
    base, wid = http_server

    status, draft = http_post(
        base, f"/api/workspaces/{wid}/capabilities",
        {"user": "admin", "key": "tune", "name": "调整能力",
         "target_type": "Vehicle",
         "implementation": {"kind": "python", "source": SOURCE_V1}})
    assert status == 200
    cid = draft["id"]

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/capabilities/{cid}",
        {"user": "admin", "name": "调整 v2",
         "implementation": {"kind": "python", "source": SOURCE_V2}})
    assert status == 200, body
    assert body["name"] == "调整 v2"
    assert body["implementation"]["source"] == SOURCE_V2

    # a viewer cannot edit
    http_post(base, f"/api/workspaces/{wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})
    status, _headers, _body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/capabilities/{cid}",
        {"user": "bob", "name": "偷偷改名"})
    assert status == 403

    # a published capability cannot be edited in place
    source = _published_recharge(platform, wid)
    status, _headers, _body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/capabilities/{source['id']}",
        {"user": "admin", "name": "改名"})
    assert status == 400

    # cloning a published capability yields a new draft over HTTP
    status, _headers, clone = http_call(
        "POST", base, f"/api/workspaces/{wid}/capabilities/{source['id']}/clone",
        {"user": "admin"})
    assert status == 200
    assert clone["key"] == "recharge_v2"
    assert clone["lifecycle"] == "draft"

    status, _headers, body = http_call(
        "POST", base, f"/api/workspaces/{wid}/capabilities/{source['id']}/clone",
        {"user": "admin", "key": "recharge"})
    assert status == 400
    assert "already exists" in body["error"]
