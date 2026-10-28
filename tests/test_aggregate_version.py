"""0.2 Aggregate versioning: optimistic concurrency + SQLite migration.

Plan-v2 acceptance (Phase 0.2):
    test_two_editors_second_gets_conflict
    test_version_monotonic
    test_conflict_maps_to_409
"""

from __future__ import annotations

import sqlite3

import pytest

from onto_platform.errors import CompilerError, ConcurrencyConflictError
from onto_platform.infrastructure.persistence.memory_repo import InMemoryRepository
from onto_platform.infrastructure.persistence.sqlite_repo import SqliteRepository
from onto_platform.interfaces.api import _status_for


def _workspace_with_ontology(platform):
    ws = platform.workspaces.create("versioning", actor="admin")
    wid = ws["id"]
    platform.ontologies.create(wid, "traffic", actor="admin")
    return wid


def _ontology_ref(platform, wid) -> tuple[str, int]:
    ontology = platform.ontologies.get(wid, "admin")
    _, version = platform.ctx.repo.get_versioned("ontology", wid, ontology["id"])
    return ontology["id"], version


def test_two_editors_second_gets_conflict(platform):
    """Two editors read version N; only the first save wins."""
    wid = _workspace_with_ontology(platform)
    _, version = _ontology_ref(platform, wid)

    # first editor: expected version matches
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"}],
        expected_version=version)

    # second editor still holds the stale version -> conflict, nothing lost
    with pytest.raises(ConcurrencyConflictError):
        platform.ontologies.upsert_object(
            wid, "admin", "TollStation",
            properties=[{"name": "name", "type": "string"}],
            expected_version=version)
    ontology = platform.ontologies.get(wid, "admin")
    assert set(ontology["objects"]) == {"Vehicle"}

    # retrying with the fresh version succeeds
    _, current = _ontology_ref(platform, wid)
    assert current == version + 1
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        properties=[{"name": "name", "type": "string"}],
        expected_version=current)
    assert set(platform.ontologies.get(wid, "admin")["objects"]) == {
        "Vehicle", "TollStation"}


def test_version_monotonic():
    repo = InMemoryRepository()
    assert repo.get_versioned("ontology", "w", "o") is None
    assert repo.put("ontology", "w", "o", {"id": "o", "n": 1}) == 1
    assert repo.put("ontology", "w", "o", {"id": "o", "n": 2}) == 2
    assert repo.get_versioned("ontology", "w", "o") == ({"id": "o", "n": 2}, 2)
    assert repo.put_if_version("ontology", "w", "o", {"id": "o", "n": 3}, 2) == 3
    with pytest.raises(ConcurrencyConflictError):
        repo.put_if_version("ontology", "w", "o", {"id": "o", "n": 4}, 2)
    # unconditional write still bumps the version
    assert repo.put_if_version("ontology", "w", "o", {"id": "o", "n": 5}, None) == 4
    assert repo.get_versioned("ontology", "w", "o")[1] == 4


def test_sqlite_migrates_documents_table_without_version(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE documents ("
        "kind TEXT NOT NULL, workspace_id TEXT, entity_id TEXT NOT NULL, "
        "data TEXT NOT NULL, PRIMARY KEY (kind, workspace_id, entity_id));"
    )
    conn.execute(
        "INSERT INTO documents VALUES ('ontology', 'w1', 'o1', '{\"id\": \"o1\"}')"
    )
    conn.commit()
    conn.close()

    repo = SqliteRepository(path)  # opening migrates the old table
    data, version = repo.get_versioned("ontology", "w1", "o1")
    assert data == {"id": "o1"}
    assert version == 0
    assert repo.put_if_version("ontology", "w1", "o1", {"id": "o1"}, 0) == 1
    with pytest.raises(ConcurrencyConflictError):
        repo.put_if_version("ontology", "w1", "o1", {"id": "o1"}, 0)


def test_sqlite_version_round_trip(tmp_path):
    repo = SqliteRepository(str(tmp_path / "v.db"))
    assert repo.put("capability", "w", "c", {"id": "c", "n": 1}) == 1
    assert repo.put("capability", "w", "c", {"id": "c", "n": 2}) == 2
    assert repo.get_versioned("capability", "w", "c") == ({"id": "c", "n": 2}, 2)


def test_conflict_maps_to_http_409():
    assert _status_for(ConcurrencyConflictError("stale")) == 409
    assert _status_for(CompilerError("bad payload")) == 422
