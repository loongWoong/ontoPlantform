"""FileCodeStore: materializes capability python source under a root dir."""

from __future__ import annotations

from pathlib import Path


class FileCodeStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, source: str) -> str:
        safe = key.replace("/", "_").replace("\\", "_")
        if not safe.endswith(".py"):
            safe += ".py"
        path = self._root / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return str(path.resolve())
