"""链二：DataSource -> Mapping/Projection -> Gateway -> Kernel。"""

from __future__ import annotations

import json

import pytest

from onto_platform.errors import ConnectorError, ValidationError
from onto_platform.infrastructure.datasource.registry import (
    connector_for,
    register,
)


def test_static_connector_roundtrip():
    connector = connector_for("static", {})
    rows = connector.fetch({"rows": [{"a": 1}, {"a": 2}]})
    assert rows == [{"a": 1}, {"a": 2}]


def test_sqlite_connector(tmp_path):
    import sqlite3

    db = tmp_path / "source.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id TEXT, v INTEGER)")
    conn.execute("INSERT INTO t VALUES ('A', 1)")
    conn.commit()
    conn.close()
    connector = connector_for("sqlite", {"path": str(db)})
    assert connector.test() is True
    rows = connector.fetch({"query": "SELECT id, v FROM t"})
    assert rows == [{"id": "A", "v": 1}]


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        connector_for("oracle", {})


def test_custom_connector_registration():
    class Dummy:
        def __init__(self, config):
            self.config = config

        def fetch(self, asset):
            return [{"ok": True}]

        def test(self):
            return True

    register("dummy", Dummy)
    assert connector_for("dummy", {}).fetch({}) == [{"ok": True}]


def test_projection_end_to_end(platform, workspace_id):
    objects = platform.ctx.gateways.for_workspace(workspace_id, {}).find_objects("Vehicle")
    assert {o["id"] for o in objects} == {"V001", "V002"}
    relations = platform.ctx.gateways.for_workspace(workspace_id, {}).related(
        "Vehicle/V001", "passes")
    assert set(relations) == {"TollStation/ST001", "TollStation/ST002"}


def test_projection_rerun_is_idempotent(platform, workspace_id):
    projections = platform.data.list_projections(workspace_id, "admin")
    first = platform.data.run_projection(workspace_id, projections[0]["id"], "admin")
    second = platform.data.run_projection(workspace_id, projections[0]["id"], "admin")
    assert first["objects_upserted"] == second["objects_upserted"]
    # rerun produces no property/link changes (all no-ops)
    assert second["changes"] == 0


def test_projection_rejects_unknown_object_type(platform, workspace_id):
    source = platform.data.list_sources(workspace_id, "admin")[0]
    with pytest.raises(ValidationError):
        platform.data.create_projection(
            workspace_id, "admin", "bad", source["id"],
            object_mappings=[{"asset": "vehicles", "object_type": "Alien",
                              "id_field": "vid"}])


def test_connector_failure_is_reported(platform, workspace_id):
    register("broken", lambda config: _Broken(config))
    source = platform.data.connect_source(
        workspace_id, "admin", "坏源", "broken", test_connection=False,
        assets=[{"name": "x", "config": {}}])
    projection = platform.data.create_projection(
        workspace_id, "admin", "bad-run", source["id"],
        object_mappings=[{"asset": "x", "object_type": "Vehicle",
                          "id_field": "vid"}])
    with pytest.raises(ConnectorError):
        platform.data.run_projection(workspace_id, projection["id"], "admin")


class _Broken:
    def __init__(self, config):
        pass

    def fetch(self, asset):
        raise OSError("boom")

    def test(self):
        return False


def test_row_filter_and_missing_ids(platform, workspace_id):
    source = platform.data.connect_source(
        workspace_id, "admin", "过滤源", "static",
        assets=[{"name": "mixed", "config": {"rows": [
            {"vid": "V100", "plate": "测A", "kind": "car"},
            {"vid": "", "plate": "测B", "kind": "car"},
            {"vid": "V200", "plate": "测C", "kind": "truck"},
        ]}}])
    projection = platform.data.create_projection(
        workspace_id, "admin", "过滤投影", source["id"],
        object_mappings=[{
            "asset": "mixed", "object_type": "Vehicle", "id_field": "vid",
            "filter": {"kind": "car"},
            "fields": [{"source_field": "plate", "property": "plate"}]}])
    summary = platform.data.run_projection(workspace_id, projection["id"], "admin")
    assert summary["objects_upserted"] == 1  # empty id skipped, truck filtered
    vehicles = platform.ctx.gateways.for_workspace(workspace_id, {}).find_objects(
        "Vehicle", {"plate": "测A"})
    assert len(vehicles) == 1
