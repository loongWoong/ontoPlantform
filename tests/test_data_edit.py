"""Second-step B: datasource editing / testing / previewing, mapping editing.

The seeded workspace has a static source (``交易库``) with three assets and a
projection (``主数据投影``) mapping them onto Vehicle/TollStation/passes.
"""

from __future__ import annotations

import pytest

from onto_platform.errors import (
    AccessDeniedError,
    ConnectorError,
    ValidationError,
)
from tests.conftest import http_call, http_get, http_post


def _source(platform, wid: str) -> dict:
    return platform.data.list_sources(wid, "admin")[0]


def _projection(platform, wid: str) -> dict:
    return platform.data.list_projections(wid, "admin")[0]


def _stored(platform, wid: str, kind: str, doc_id: str) -> dict:
    return platform.ctx.repo.get(kind, wid, doc_id)


def _actions(platform, wid: str) -> list[str]:
    return [entry["action"] for entry in platform.ctx.audit.list(wid, limit=500)]


# ----------------------------------------------------------------------
# UpdateDataSource + PATCH route
# ----------------------------------------------------------------------


def test_update_source_renames_and_reconfigures(platform, workspace_id):
    wid = workspace_id
    source = _source(platform, wid)

    updated = platform.data.update_source(
        wid, "admin", source["id"], name="  交易库v2  ", config={"tag": "v2"})

    assert updated["id"] == source["id"]
    assert updated["name"] == "交易库v2"
    assert updated["config"] == {"tag": "v2"}
    assert updated["kind"] == "static"
    assert sorted(updated["assets"]) == ["stations", "transactions", "vehicles"]
    assert _stored(platform, wid, "datasource", source["id"])["name"] == "交易库v2"
    assert "data.update_source" in _actions(platform, wid)


def test_update_source_rejects_invalid_input_and_preserves_document(platform,
                                                                  workspace_id):
    wid = workspace_id
    source = _source(platform, wid)
    before = _stored(platform, wid, "datasource", source["id"])

    with pytest.raises(ValidationError, match="kind"):
        platform.data.update_source(wid, "admin", source["id"], kind="sqlite")
    with pytest.raises(ValidationError, match="nothing to update"):
        platform.data.update_source(wid, "admin", source["id"])
    with pytest.raises(ValidationError, match="name"):
        platform.data.update_source(wid, "admin", source["id"], name="   ")
    with pytest.raises(ValidationError, match="config"):
        platform.data.update_source(wid, "admin", source["id"], config=["x"])
    with pytest.raises(ValidationError, match="duplicate"):
        platform.data.update_source(
            wid, "admin", source["id"],
            assets=[{"name": "a", "config": {}}, {"name": "a", "config": {}}])
    with pytest.raises(ValidationError, match="empty"):
        platform.data.update_source(
            wid, "admin", source["id"],
            assets=[{"name": " ", "config": {}}])

    assert _stored(platform, wid, "datasource", source["id"]) == before


def test_update_source_protects_assets_used_by_projections(platform,
                                                           workspace_id):
    wid = workspace_id
    source = _source(platform, wid)
    before = _stored(platform, wid, "datasource", source["id"])
    assets = [source["assets"][name] for name in sorted(source["assets"])]
    without_vehicles = [a for a in assets if a["name"] != "vehicles"]

    with pytest.raises(ValidationError) as excinfo:
        platform.data.update_source(
            wid, "admin", source["id"], assets=without_vehicles)
    message = str(excinfo.value)
    assert "vehicles" in message and "主数据投影" in message
    assert _stored(platform, wid, "datasource", source["id"]) == before

    # an unreferenced asset can be added and removed again
    added = platform.data.update_source(
        wid, "admin", source["id"],
        assets=assets + [{"name": "extra", "config": {"rows": []}}])
    assert "extra" in added["assets"]
    removed = platform.data.update_source(
        wid, "admin", source["id"], assets=assets)
    assert "extra" not in removed["assets"]
    assert sorted(removed["assets"]) == ["stations", "transactions", "vehicles"]


def test_update_source_connection_failure_keeps_old_document(platform,
                                                             workspace_id,
                                                             tmp_path):
    wid = workspace_id
    source = platform.data.connect_source(
        wid, "admin", "磁盘源", "sqlite",
        config={"path": str(tmp_path / "source.db")},
        assets=[{"name": "t", "config": {"query": "SELECT 1"}}],
        test_connection=False)
    before = _stored(platform, wid, "datasource", source["id"])
    missing_dir = str(tmp_path / "missing" / "source.db")

    with pytest.raises(ConnectorError):
        platform.data.update_source(
            wid, "admin", source["id"], config={"path": missing_dir})

    # the failed connection check must not overwrite the stored source
    assert _stored(platform, wid, "datasource", source["id"]) == before


def test_update_source_requires_editor(platform, workspace_id):
    wid = workspace_id
    source = _source(platform, wid)
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")
    before = _stored(platform, wid, "datasource", source["id"])

    with pytest.raises(AccessDeniedError):
        platform.data.update_source(wid, "bob", source["id"], name="x")
    assert _stored(platform, wid, "datasource", source["id"]) == before


def test_test_source_returns_ok_and_wraps_failure(platform, workspace_id,
                                                  tmp_path):
    wid = workspace_id
    source = _source(platform, wid)

    result = platform.data.test_source(wid, "admin", source["id"])
    assert result["ok"] is True
    assert result["kind"] == "static"
    assert result["tested_at"]

    broken = platform.data.connect_source(
        wid, "admin", "坏磁盘源", "sqlite",
        config={"path": str(tmp_path / "missing2" / "db.sqlite")},
        assets=[], test_connection=False)
    with pytest.raises(ConnectorError):
        platform.data.test_source(wid, "admin", broken["id"])


def test_preview_source_returns_real_rows_and_clamps_limit(platform,
                                                          workspace_id):
    wid = workspace_id
    source = _source(platform, wid)

    view = platform.data.preview_source(wid, "admin", source["id"], "vehicles")
    assert view["asset"] == "vehicles"
    assert view["total"] == 2
    assert view["columns"] == ["vid", "plate", "balance"]
    assert [row["vid"] for row in view["rows"]] == ["V001", "V002"]

    one = platform.data.preview_source(
        wid, "admin", source["id"], "vehicles", limit=1)
    assert len(one["rows"]) == 1 and one["total"] == 2

    zero = platform.data.preview_source(
        wid, "admin", source["id"], "vehicles", limit=0)
    assert len(zero["rows"]) == 1

    big = platform.data.preview_source(
        wid, "admin", source["id"], "vehicles", limit=1000)
    assert len(big["rows"]) == 2

    with pytest.raises(ValidationError, match="integer"):
        platform.data.preview_source(
            wid, "admin", source["id"], "vehicles", limit="abc")
    with pytest.raises(ValidationError, match="unknown asset"):
        platform.data.preview_source(wid, "admin", source["id"], "nope")

    # preview is read-only: no document changed, nothing projected
    before_objects = platform.ctx.gateways.for_workspace(wid, {}).find_objects(
        "Vehicle", {})
    after_objects = platform.ctx.gateways.for_workspace(wid, {}).find_objects(
        "Vehicle", {})
    assert before_objects == after_objects


def test_preview_sqlite_source_reads_the_real_database(platform, workspace_id,
                                                       tmp_path):
    import sqlite3

    db_path = str(tmp_path / "preview.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE vehicles (vid TEXT, plate TEXT, balance INT)")
        conn.executemany(
            "INSERT INTO vehicles VALUES (?, ?, ?)",
            [("V1", "鲁A1", 10), ("V2", "鲁B2", 20), ("V3", "鲁C3", 30)])
        conn.commit()
    finally:
        conn.close()
    wid = workspace_id
    source = platform.data.connect_source(
        wid, "admin", "真实库", "sqlite", config={"path": db_path},
        assets=[{"name": "all", "config": {
            "query": "SELECT vid, plate, balance FROM vehicles ORDER BY vid"}}],
        test_connection=False)

    assert platform.data.test_source(wid, "admin", source["id"])["ok"] is True
    view = platform.data.preview_source(wid, "admin", source["id"], "all",
                                        limit=2)
    assert view["total"] == 3
    assert view["columns"] == ["vid", "plate", "balance"]
    assert view["rows"] == [{"vid": "V1", "plate": "鲁A1", "balance": 10},
                            {"vid": "V2", "plate": "鲁B2", "balance": 20}]


# ----------------------------------------------------------------------
# UpdateProjection + PATCH route
# ----------------------------------------------------------------------


def test_update_projection_changes_metadata_and_mappings(platform,
                                                         workspace_id):
    wid = workspace_id
    projection = _projection(platform, wid)

    updated = platform.data.update_projection(
        wid, "admin", projection["id"], name="  主数据投影v2  ",
        mode="authoritative", row_limit=5)
    assert updated["name"] == "主数据投影v2"
    assert updated["mode"] == "authoritative"
    assert updated["row_limit"] == 5
    assert updated["datasource_id"] == projection["datasource_id"]
    assert "data.update_projection" in _actions(platform, wid)

    mapped = platform.data.update_projection(
        wid, "admin", projection["id"],
        object_mappings=[{
            "asset": "vehicles", "object_type": "Vehicle", "id_field": "vid",
            "fields": [{"source_field": "plate", "property": "plate"}]}])
    assert len(mapped["object_mappings"]) == 1
    assert len(mapped["relation_mappings"]) == 1
    assert _stored(platform, wid, "projection", projection["id"])["mode"] \
        == "authoritative"


def test_update_projection_rejects_bad_definition_and_preserves_old(platform,
                                                                    workspace_id):
    wid = workspace_id
    projection = _projection(platform, wid)
    before = _stored(platform, wid, "projection", projection["id"])

    with pytest.raises(ValidationError, match="nothing to update"):
        platform.data.update_projection(wid, "admin", projection["id"])
    with pytest.raises(ValidationError, match="mode"):
        platform.data.update_projection(
            wid, "admin", projection["id"], mode="fuzzy")
    with pytest.raises(ValidationError, match="row_limit"):
        platform.data.update_projection(
            wid, "admin", projection["id"], row_limit="abc")
    with pytest.raises(ValidationError, match="datasource cannot be changed"):
        platform.data.update_projection(
            wid, "admin", projection["id"], datasource_id="other-source")
    with pytest.raises(ValidationError, match="unknown object type"):
        platform.data.update_projection(
            wid, "admin", projection["id"],
            object_mappings=[{"asset": "vehicles", "object_type": "Alien",
                              "id_field": "vid", "fields": []}])
    with pytest.raises(ValidationError, match="unknown asset"):
        platform.data.update_projection(
            wid, "admin", projection["id"],
            object_mappings=[{"asset": "nope", "object_type": "Vehicle",
                              "id_field": "vid", "fields": []}])
    with pytest.raises(ValidationError, match="unknown relation"):
        platform.data.update_projection(
            wid, "admin", projection["id"],
            relation_mappings=[{"asset": "transactions", "relation": "nope",
                                "source_field": "vid", "target_field": "sid"}])

    assert _stored(platform, wid, "projection", projection["id"]) == before


def test_update_projection_rejects_identity_mismatch(platform):
    """id_field must equal the identity declared by the TBox (O-1)."""
    wid = platform.workspaces.create("身份投影", actor="admin")["id"]
    platform.ontologies.create(wid, "ident", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string", "required": True},
                    {"name": "balance", "type": "number"}],
        identity=["plate"])
    platform.ontologies.publish(wid, "admin")
    source = platform.data.connect_source(
        wid, "admin", "identity-src", "static",
        assets=[{"name": "v", "config": {"rows": []}}])
    projection = platform.data.create_projection(
        wid, "admin", "identity-projection", source["id"],
        object_mappings=[{"asset": "v", "object_type": "Vehicle",
                          "id_field": "plate", "fields": []}])
    before = _stored(platform, wid, "projection", projection["id"])

    with pytest.raises(ValidationError, match="id_field"):
        platform.data.update_projection(
            wid, "admin", projection["id"],
            object_mappings=[{"asset": "v", "object_type": "Vehicle",
                              "id_field": "vid", "fields": []}])
    assert _stored(platform, wid, "projection", projection["id"]) == before


def test_update_projection_requires_editor(platform, workspace_id):
    wid = workspace_id
    projection = _projection(platform, wid)
    platform.workspaces.add_member(wid, "bob", "viewer", "admin")
    before = _stored(platform, wid, "projection", projection["id"])

    with pytest.raises(AccessDeniedError):
        platform.data.update_projection(wid, "bob", projection["id"], mode="additive")
    assert _stored(platform, wid, "projection", projection["id"]) == before


# ----------------------------------------------------------------------
# HTTP contract
# ----------------------------------------------------------------------


def test_data_edit_over_http(http_server):
    base, wid = http_server
    sources = http_get(base, f"/api/workspaces/{wid}/datasources?user=admin")[1]
    source = sources[0]
    projections = http_get(
        base, f"/api/workspaces/{wid}/projections?user=admin")[1]
    projection = projections[0]

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/datasources/{source['id']}",
        {"user": "admin", "name": "交易库HTTP"})
    assert status == 200
    assert body["name"] == "交易库HTTP"

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/datasources/{source['id']}",
        {"user": "admin", "kind": "sqlite"})
    assert status == 400
    assert "kind" in body["error"]

    status, body = http_post(
        base, f"/api/workspaces/{wid}/datasources/{source['id']}/test",
        {"user": "admin"})
    assert status == 200
    assert body["ok"] is True and body["kind"] == "static"

    status, body = http_post(
        base, f"/api/workspaces/{wid}/datasources/{source['id']}/preview",
        {"user": "admin", "asset": "vehicles", "limit": 1})
    assert status == 200
    assert body["total"] == 2 and len(body["rows"]) == 1
    assert body["columns"] == ["vid", "plate", "balance"]

    status, body = http_post(
        base, f"/api/workspaces/{wid}/datasources/{source['id']}/preview",
        {"user": "admin", "asset": "nope"})
    assert status == 400
    assert "unknown asset" in body["error"]

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/projections/{projection['id']}",
        {"user": "admin", "mode": "authoritative"})
    assert status == 200
    assert body["mode"] == "authoritative"

    status, _headers, body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/projections/{projection['id']}",
        {"user": "admin",
         "object_mappings": [{"asset": "vehicles", "object_type": "Alien",
                              "id_field": "vid", "fields": []}]})
    assert status == 400
    assert "unknown object type" in body["error"]

    http_post(base, f"/api/workspaces/{wid}/members",
              {"user": "admin", "member": "bob", "role": "viewer"})
    status, _headers, _body = http_call(
        "PATCH", base, f"/api/workspaces/{wid}/datasources/{source['id']}",
        {"user": "bob", "name": "越权"})
    assert status == 403
