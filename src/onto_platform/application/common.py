"""Application layer: use cases, organized by user goal (design.txt §38).

Not "big services" - each use case is one user intention:

    CreateOntology / ValidateOntology / PublishOntology
    ConnectDataSource / CreateProjection / RunProjection
    CreateCapability / PublishCapability / InvokeCapability
    CreateApplication / PublishApplication / RunAppQuery
    AskOntology / RunAgent
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from ..domain.governance.model import AccessPolicy, Membership
from ..errors import NotFoundError
from ..ports.llm import LLMProvider
from ..ports.runtime import GatewayProvider
from ..ports.storage import AuditLog, CodeStore, Repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid4().hex[:12]


@dataclass
class ApplicationContext:
    """Everything a use case may touch - and nothing more."""

    repo: Repository
    audit: AuditLog
    gateways: GatewayProvider
    code_store: CodeStore
    default_runtime_config: dict = field(default_factory=dict)
    llm: LLMProvider | None = None
    policy: AccessPolicy = field(default_factory=AccessPolicy)
    clock: Callable[[], str] = _now
    id_factory: Callable[[], str] = _new_id

    # ---- governance helpers ----

    def require(self, actor: str, workspace_id: str, role: str = "editor") -> None:
        memberships = [
            Membership.from_dict(m)
            for m in self.repo.list("membership", workspace_id)
        ]
        self.policy.check(memberships, actor, workspace_id, role)

    def audit_event(self, actor: str, workspace_id: str, action: str,
                    target: str, detail: dict | None = None) -> None:
        self.audit.append(
            {
                "ts": self.clock(),
                "actor": actor,
                "workspace_id": workspace_id,
                "action": action,
                "target": target,
                "detail": detail or {},
            }
        )

    # ---- repository helpers ----

    def must_get(self, kind: str, workspace_id: str, entity_id: str) -> dict:
        data = self.repo.get(kind, workspace_id, entity_id)
        if data is None:
            raise NotFoundError(f"{kind} not found: {entity_id!r}")
        return data

    def save(self, kind: str, data: dict) -> None:
        self.repo.put(kind, data.get("workspace_id"), data["id"], data)

    # ---- runtime access (with schema self-healing) ----

    def gateway_for(self, workspace_id: str):
        """The workspace's gateway, republishing the stored schema payload
        when this process has never seen it (process restart / new instance).
        Definitions live in the repository; the kernel rebuilds from them."""
        workspace = self.repo.get("workspace", None, workspace_id)
        runtime_config = (workspace or {}).get("runtime_config", {})
        gateway = self.gateways.for_workspace(workspace_id, runtime_config)
        if not getattr(gateway, "published", True):
            payload = self.repo.get("schema_payload", workspace_id, workspace_id)
            if payload:
                from ..ports.runtime import SchemaPayload

                gateway.publish_schema(
                    SchemaPayload(
                        schema=payload["schema"],
                        bindings=payload.get("bindings", {}),
                        workflows=payload.get("workflows", {}),
                    )
                )
        return gateway
