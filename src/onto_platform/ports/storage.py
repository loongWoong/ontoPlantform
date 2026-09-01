"""Storage Ports: persistence of platform definitions.

Platform definitions (Workspace / Ontology / DataSource / Projection /
Capability / Application / Membership) are documents keyed by
``(kind, workspace_id, id)``.  Semantic state is NOT stored here -
the kernel owns it (design.txt §16/§47 原则一).
"""

from __future__ import annotations

from typing import Protocol


class Repository(Protocol):

    def put(self, kind: str, workspace_id: str | None, entity_id: str, data: dict) -> None:
        """Insert or overwrite one document."""
        ...

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None: ...

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool: ...

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        """All documents of a kind, optionally scoped to a workspace."""
        ...


class CodeStore(Protocol):
    """Materializes capability source code (python implementations).

    Inline source written at publish time; the kernel's python executor
    loads the returned entrypoint path.
    """

    def put(self, key: str, source: str) -> str:
        """Persist source under key; return the entrypoint path."""
        ...


class AuditLog(Protocol):
    """谁 / 什么时候 / 操作了什么 / 产生了什么变化 (design.txt §29)."""

    def append(self, entry: dict) -> None: ...

    def list(self, workspace_id: str | None = None, limit: int = 100) -> list[dict]: ...
