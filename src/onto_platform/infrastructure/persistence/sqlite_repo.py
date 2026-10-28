"""SQLite Repository + AuditLog: durable platform definitions.

One generic document table keeps the persistence layer tiny; semantic
state deliberately does NOT live here - the kernel owns it
(design.txt §16/§47 原则一).  Every document carries an aggregate
``version`` used for optimistic concurrency control.
"""

from __future__ import annotations

import json
import sqlite3
import threading

from ...errors import ConcurrencyConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    kind         TEXT NOT NULL,
    workspace_id TEXT,
    entity_id    TEXT NOT NULL,
    data         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 0,
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


def _migrate_documents(conn: sqlite3.Connection) -> None:
    """Add the ``version`` column to databases created before v0.2."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "version" not in columns:
        conn.execute(
            "ALTER TABLE documents ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
        )


class SqliteRepository:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        _migrate_documents(self._conn)
        self._conn.commit()

    def close(self) -> None:
        """Release the SQLite handle so a restarted process can reopen it."""
        with self._lock:
            self._conn.close()

    def append_audit(self, entry: dict) -> None:
        """Write an audit row on this repository's connection.

        The audit log and the documents table live in the same SQLite file.
        A second connection cannot write while a repository transaction holds
        ``BEGIN IMMEDIATE``; sharing the connection keeps an audit event in
        the same transaction as the state change it describes.
        """
        params = (
            entry.get("ts", ""),
            entry.get("actor", ""),
            entry.get("workspace_id"),
            entry.get("action", ""),
            entry.get("target") or "",
            json.dumps(entry.get("detail", {}), ensure_ascii=False),
        )
        if self._in_tx():
            with self._lock:
                self._conn.execute(
                    "INSERT INTO audit_log "
                    "(ts, actor, workspace_id, action, target, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)", params)
        else:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO audit_log "
                    "(ts, actor, workspace_id, action, target, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)", params)

    # ---- transactions ----

    def transaction(self) -> "_SqliteTransaction":
        return _SqliteTransaction(self)

    def _in_tx(self) -> bool:
        return getattr(self._local, "depth", 0) > 0

    def _begin(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            self._lock.acquire()
            self._conn.execute("BEGIN IMMEDIATE")
            self._local.depth = 1
        else:
            self._local.depth = depth + 1

    def _end(self, commit: bool) -> None:
        depth = getattr(self._local, "depth", 1) - 1
        if depth > 0:
            self._local.depth = depth
            return
        self._local.depth = 0
        try:
            if commit:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._lock.release()

    # ---- Repository Port ----

    def put(self, kind: str, workspace_id: str | None, entity_id: str, data: dict) -> int:
        if self._in_tx():
            with self._lock:
                version = self._current_version(kind, workspace_id, entity_id) + 1
                self._write(kind, workspace_id, entity_id, data, version)
                return version
        with self._lock, self._conn:
            version = self._current_version(kind, workspace_id, entity_id) + 1
            self._write(kind, workspace_id, entity_id, data, version)
            return version

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM documents WHERE kind = ? AND workspace_id IS ? "
                "AND entity_id = ?",
                (kind, workspace_id, entity_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_versioned(
        self, kind: str, workspace_id: str | None, entity_id: str
    ) -> tuple[dict, int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT data, version FROM documents WHERE kind = ? "
                "AND workspace_id IS ? AND entity_id = ?",
                (kind, workspace_id, entity_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), int(row[1])

    def put_if_version(
        self,
        kind: str,
        workspace_id: str | None,
        entity_id: str,
        data: dict,
        expected_version: int | None,
    ) -> int:
        if self._in_tx():
            with self._lock:
                current = self._current_version(kind, workspace_id, entity_id)
                if expected_version is not None and expected_version != current:
                    raise ConcurrencyConflictError(
                        f"{kind} {entity_id!r} was modified concurrently "
                        f"(expected version {expected_version}, current {current})"
                    )
                version = current + 1
                self._write(kind, workspace_id, entity_id, data, version)
                return version
        with self._lock, self._conn:
            current = self._current_version(kind, workspace_id, entity_id)
            if expected_version is not None and expected_version != current:
                raise ConcurrencyConflictError(
                    f"{kind} {entity_id!r} was modified concurrently "
                    f"(expected version {expected_version}, current {current})"
                )
            version = current + 1
            self._write(kind, workspace_id, entity_id, data, version)
            return version

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool:
        if self._in_tx():
            with self._lock:
                cur = self._conn.execute(
                    "DELETE FROM documents WHERE kind = ? AND workspace_id IS ? "
                    "AND entity_id = ?",
                    (kind, workspace_id, entity_id),
                )
                return cur.rowcount > 0
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

    # ---- internals ----

    def _current_version(
        self, kind: str, workspace_id: str | None, entity_id: str
    ) -> int:
        row = self._conn.execute(
            "SELECT version FROM documents WHERE kind = ? AND workspace_id IS ? "
            "AND entity_id = ?",
            (kind, workspace_id, entity_id),
        ).fetchone()
        return int(row[0]) if row else 0

    def _write(
        self,
        kind: str,
        workspace_id: str | None,
        entity_id: str,
        data: dict,
        version: int,
    ) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        updated = self._conn.execute(
            "UPDATE documents SET data = ?, version = ? WHERE kind = ? "
            "AND workspace_id IS ? AND entity_id = ?",
            (payload, version, kind, workspace_id, entity_id),
        ).rowcount
        if not updated:
            self._conn.execute(
                "INSERT INTO documents (kind, workspace_id, entity_id, data, version) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, workspace_id, entity_id, payload, version),
            )


class _SqliteTransaction:
    """Context manager returned by ``SqliteRepository.transaction()``.

    One real SQLite transaction on the shared connection: documents, releases,
    deployment pointers and outbox events either all commit or all vanish
    (plan-v2 1.2/1.3)."""

    def __init__(self, repo: SqliteRepository) -> None:
        self._repo = repo

    def __enter__(self):
        self._repo._begin()
        return self._repo

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._repo._end(commit=exc_type is None)
        return False


class SqliteAuditLog:
    def __init__(self, path: str,
                 repository: "SqliteRepository | None" = None) -> None:
        self._lock = threading.Lock()
        self._repository = repository
        if repository is not None:
            # share the repository connection so an audit row written inside
            # a repository transaction cannot deadlock on the same file
            self._conn = repository._conn
        else:
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            _migrate_documents(self._conn)
            self._conn.commit()

    def close(self) -> None:
        if self._repository is not None:
            return
        with self._lock:
            self._conn.close()

    def append(self, entry: dict) -> None:
        if self._repository is not None:
            self._repository.append_audit(entry)
            return
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
        lock = self._repository._lock if self._repository is not None else self._lock
        with lock:
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
