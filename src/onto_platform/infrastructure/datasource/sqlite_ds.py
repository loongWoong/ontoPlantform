"""SqliteConnector: a SQL database as a DataSource (stdlib sqlite3)."""

from __future__ import annotations

import sqlite3


class SqliteConnector:
    def __init__(self, config: dict) -> None:
        self._path = config.get("path")
        if not self._path:
            raise ValueError("sqlite datasource config requires 'path'")

    def fetch(self, asset: dict) -> list[dict]:
        query = asset.get("query")
        if not query:
            raise ValueError("sqlite asset config requires 'query'")
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(query)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def test(self) -> bool:
        conn = sqlite3.connect(self._path)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return True
