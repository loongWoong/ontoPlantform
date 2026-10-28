"""In-memory Repository + AuditLog: default for tests and demos.

The repository is thread-safe and supports nested-free transactions
(``with repo.transaction():``): writes inside the block become visible only
when it commits, and are rolled back on an exception.  This is what makes
"aggregate + outbox event in one transaction" real on the memory backend
instead of a comment (plan-v2 1.2).
"""

from __future__ import annotations

import threading

from ...errors import ConcurrencyConflictError


class InMemoryRepository:
    """Documents keyed by (kind, workspace_id, id), versioned (optimistic lock)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._data: dict[tuple[str, str | None, str], dict] = {}
        self._versions: dict[tuple[str, str | None, str], int] = {}

    # ---- transactions ----

    def transaction(self) -> "_MemoryTransaction":
        return _MemoryTransaction(self)

    def _begin(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            self._lock.acquire()
            self._local.snapshot = (dict(self._data), dict(self._versions))
            self._local.depth = 1
        else:
            self._local.depth = depth + 1

    def _end(self, commit: bool) -> None:
        depth = getattr(self._local, "depth", 1) - 1
        if depth > 0:
            self._local.depth = depth
            return
        self._local.depth = 0
        if not commit:
            data, versions = self._local.snapshot
            self._data.clear()
            self._data.update(data)
            self._versions.clear()
            self._versions.update(versions)
        self._local.snapshot = None
        self._lock.release()

    # ---- Repository Port ----

    def put(self, kind: str, workspace_id: str | None, entity_id: str, data: dict) -> int:
        with self._lock:
            key = (kind, workspace_id, entity_id)
            version = self._versions.get(key, 0) + 1
            self._data[key] = dict(data)
            self._versions[key] = version
            return version

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None:
        with self._lock:
            data = self._data.get((kind, workspace_id, entity_id))
            return dict(data) if data is not None else None

    def get_versioned(
        self, kind: str, workspace_id: str | None, entity_id: str
    ) -> tuple[dict, int] | None:
        with self._lock:
            key = (kind, workspace_id, entity_id)
            data = self._data.get(key)
            if data is None:
                return None
            return dict(data), self._versions.get(key, 1)

    def put_if_version(
        self,
        kind: str,
        workspace_id: str | None,
        entity_id: str,
        data: dict,
        expected_version: int | None,
    ) -> int:
        with self._lock:
            key = (kind, workspace_id, entity_id)
            current = self._versions.get(key, 0)
            if expected_version is not None and expected_version != current:
                raise ConcurrencyConflictError(
                    f"{kind} {entity_id!r} was modified concurrently "
                    f"(expected version {expected_version}, current {current})"
                )
            version = current + 1
            self._data[key] = dict(data)
            self._versions[key] = version
            return version

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool:
        with self._lock:
            key = (kind, workspace_id, entity_id)
            self._versions.pop(key, None)
            return self._data.pop(key, None) is not None

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        with self._lock:
            return [
                dict(data)
                for (k, ws, _), data in self._data.items()
                if k == kind and (workspace_id is None or ws == workspace_id)
            ]


class _MemoryTransaction:
    """Context manager returned by ``InMemoryRepository.transaction()``."""

    def __init__(self, repo: InMemoryRepository) -> None:
        self._repo = repo

    def __enter__(self):
        self._repo._begin()
        return self._repo

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._repo._end(commit=exc_type is None)
        return False


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict] = []

    def append(self, entry: dict) -> None:
        with self._lock:
            self._entries.append(dict(entry))

    def list(self, workspace_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            entries = [
                e for e in self._entries
                if workspace_id is None or e.get("workspace_id") == workspace_id
            ]
            return [dict(e) for e in entries[-limit:]]
