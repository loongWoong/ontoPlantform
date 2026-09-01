"""SQLite Repository + AuditLog: durable platform definitions.

One generic document table keeps the persistence layer tiny; semantic
state deliberately does NOT live here - the kernel owns it
(design.txt §16/§47 原则一).
"""

from __future__ import annotations

import json
import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    kind         TEXT NOT NULL,
    workspace_id TEXT,
    entity_id    TEXT NOT NULL,
    data         TEXT NOT NULL,
    PRIMARY KEY (kind, workspace_id, entity_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    actor  TEXT NOT NULL,
    workspace_id TEXT,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class SqliteRepository:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put(self, kind: str, workspace_id: str | None, entity_id: str, data: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)",
                (kind, workspace_id, entity_id,
                 json.dumps(data, ensure_ascii=False)),
            )

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM documents WHERE kind = ? AND workspace_id IS ? "
                "AND entity_id = ?",
                (kind, workspace_id, entity_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM documents WHERE kind = ? AND workspace_id IS ? "
                "AND entity_id = ?",
                (kind, workspace_id, entity_id),
            )
        return cur.rowcount > 0

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        with self._lock:
            if workspace_id is None:
                rows = self._conn.execute(
                    "SELECT data FROM documents WHERE kind = ? ORDER BY rowid",
                    (kind,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT data FROM documents WHERE kind = ? AND workspace_id IS ? "
                    "ORDER BY rowid",
                    (kind, workspace_id),
                ).fetchall()
        return [json.loads(r[0]) for r in rows]


class SqliteAuditLog:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def append(self, entry: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit_log (ts, actor, workspace_id, action, target, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.get("ts", ""),
                    entry.get("actor", ""),
                    entry.get("workspace_id"),
                    entry.get("action", ""),
                    entry.get("target", ""),
                    json.dumps(entry.get("detail", {}), ensure_ascii=False),
                ),
            )

    def list(self, workspace_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if workspace_id is None:
                rows = self._conn.execute(
                    "SELECT ts, actor, workspace_id, action, target, detail "
                    "FROM audit_log ORDER BY seq DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT ts, actor, workspace_id, action, target, detail "
                    "FROM audit_log WHERE workspace_id IS ? ORDER BY seq DESC LIMIT ?",
                    (workspace_id, limit),
                ).fetchall()
        return [
            {
                "ts": r[0], "actor": r[1], "workspace_id": r[2],
                "action": r[3], "target": r[4], "detail": json.loads(r[5]),
            }
            for r in rows
        ]
