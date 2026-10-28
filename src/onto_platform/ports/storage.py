"""Storage Ports: persistence of platform definitions.

Platform definitions (Workspace / Ontology / DataSource / Projection /
Capability / Application / Membership) are documents keyed by
``(kind, workspace_id, id)``.  Semantic state is NOT stored here -
the kernel owns it (design.txt §16/§47 原则一).
"""

from __future__ import annotations

from typing import Protocol


class Repository(Protocol):

    def put(self, kind: str, workspace_id: str | None, entity_id: str, data: dict) -> int:
        """Insert or overwrite one document; return the new aggregate version."""
        ...

    def get(self, kind: str, workspace_id: str | None, entity_id: str) -> dict | None: ...

    def get_versioned(
        self, kind: str, workspace_id: str | None, entity_id: str
    ) -> tuple[dict, int] | None:
        """Like ``get`` but also returns the aggregate version (0 = absent)."""
        ...

    def put_if_version(
        self,
        kind: str,
        workspace_id: str | None,
        entity_id: str,
        data: dict,
        expected_version: int | None,
    ) -> int:
        """Optimistic-lock write.

        ``expected_version=None`` means unconditional.  Otherwise the write
        only succeeds when the stored version equals ``expected_version``;
        a stale version raises ``ConcurrencyConflictError``.  Returns the new
        version.
        """
        ...

    def delete(self, kind: str, workspace_id: str | None, entity_id: str) -> bool: ...

    def list(self, kind: str, workspace_id: str | None = None) -> list[dict]:
        """All documents of a kind, optionally scoped to a workspace."""
        ...

    def transaction(self):
        """Context manager grouping writes into one atomic commit.

        Used for aggregate + outbox event (memory: copy-on-write snapshot;
        sqlite: one BEGIN IMMEDIATE transaction).  Nested use is allowed and
        joins the outer transaction.
        """
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
