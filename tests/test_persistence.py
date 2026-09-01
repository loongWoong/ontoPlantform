"""Persistence: memory vs sqlite repositories, code store, audit log。"""

from __future__ import annotations

from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.infrastructure.persistence.file_code_store import FileCodeStore
from onto_platform.infrastructure.persistence.memory_repo import (
    InMemoryAuditLog,
    InMemoryRepository,
)
from onto_platform.infrastructure.persistence.sqlite_repo import (
    SqliteAuditLog,
    SqliteRepository,
)


def test_memory_repository_roundtrip():
    repo = InMemoryRepository()
    repo.put("ontology", "w1", "o1", {"name": "traffic"})
    assert repo.get("ontology", "w1", "o1") == {"name": "traffic"}
    assert repo.list("ontology", "w1") == [{"name": "traffic"}]
    assert repo.list("ontology", "w2") == []
    repo.put("ontology", "w1", "o1", {"name": "traffic2"})
    assert repo.get("ontology", "w1", "o1") == {"name": "traffic2"}
    assert repo.delete("ontology", "w1", "o1") is True
    assert repo.delete("ontology", "w1", "o1") is False


def test_sqlite_repository_roundtrip(tmp_path):
    repo = SqliteRepository(str(tmp_path / "p.db"))
    repo.put("ontology", "w1", "o1", {"name": "交通", "objects": {"A": {}}})
    assert repo.get("ontology", "w1", "o1") == {"name": "交通", "objects": {"A": {}}}
    assert repo.list("ontology", "w1") == [{"name": "交通", "objects": {"A": {}}}]


def test_sqlite_and_memory_repositories_agree(tmp_path):
    docs = [{"kind": "ontology", "id": "o1", "data": {"name": "a"}},
            {"kind": "ontology", "id": "o2", "data": {"name": "b"}}]
    for repo in (InMemoryRepository(), SqliteRepository(str(tmp_path / "q.db"))):
        for doc in docs:
            repo.put(doc["kind"], "w1", doc["id"], doc["data"])
        assert {d["name"] for d in repo.list("ontology", "w1")} == {"a", "b"}


def test_audit_logs(tmp_path):
    for log in (InMemoryAuditLog(), SqliteAuditLog(str(tmp_path / "a.db"))):
        log.append({"ts": "t1", "actor": "admin", "workspace_id": "w1",
                    "action": "x", "target": "y", "detail": {"k": 1}})
        log.append({"ts": "t2", "actor": "bob", "workspace_id": "w2",
                    "action": "z", "target": "w", "detail": {}})
        assert len(log.list("w1")) == 1
        assert len(log.list()) == 2
        assert log.list(limit=1)[0]["action"] == "z"  # newest first


def test_file_code_store(tmp_path):
    store = FileCodeStore(tmp_path / "code")
    path = store.put("ws1/Vehicle.recharge", "def execute(ctx):\n    return 1\n")
    assert path.endswith(".py")
    assert "def execute" in open(path, encoding="utf-8").read()


def test_sqlite_platform_end_to_end(tmp_path):
    """The whole platform works over sqlite persistence."""
    platform = build_platform(PlatformConfig(
        store="sqlite", db_path=str(tmp_path / "platform.db")))
    ws = platform.workspaces.create("制造运营", actor="admin")
    platform.ontologies.create(ws["id"], "manufacturing", actor="admin")
    platform.ontologies.upsert_object(
        ws["id"], "admin", "Equipment",
        properties=[{"name": "code", "type": "string"}])
    platform.ontologies.publish(ws["id"], "admin")
    # a second platform instance over the same db sees the same world
    platform2 = build_platform(PlatformConfig(
        store="sqlite", db_path=str(tmp_path / "platform.db")))
    ontology = platform2.ontologies.get(ws["id"], "admin")
    assert ontology["objects"]["Equipment"]["properties"]["code"]["type"] == "string"
    assert platform2.ontologies.describe_runtime(ws["id"], "admin")["objects"]
