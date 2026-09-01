"""StaticConnector: rows stored in the source config itself.

Zero-dependency workhorse for demos, tests and small reference data.
"""

from __future__ import annotations


class StaticConnector:
    def __init__(self, config: dict) -> None:
        self._config = config

    def fetch(self, asset: dict) -> list[dict]:
        rows = asset.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("static asset 'rows' must be a list")
        return [dict(row) for row in rows]

    def test(self) -> bool:
        return True
