"""In-memory Repository + AuditLog: default for tests and demos."""

from __future__ import annotations

import threading


class InMemoryRepository:
    """Documents keyed by (kind, workspace_id, id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple[str, str | None, str], dict] = {}

    def put(self, kind: str, workspace_id: str | None, entity_id: str, data: dict) -> None:
        with self._lock:
            self._data[(kind, workspace_id, entity_id)] = dict(data)

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None:
        with self._lock:
            data = self._data.get((kind, workspace_id, entity_id))
            return dict(data) if data is not None else None

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool:
        with self._lock:
            return self._data.pop((kind, workspace_id, entity_id), None) is not None

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        with self._lock:
            return [
                dict(data)
                for (k, ws, _), data in self._data.items()
                if k == kind and (workspace_id is None or ws == workspace_id)
            ]


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
