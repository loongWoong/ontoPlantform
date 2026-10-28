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
from .limits import LeaseManager, RateLimiter


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
    #: identifies this process for execution-ledger ownership and stale-claim
    #: recovery (P0-2); a restart gets a new value
    instance_id: str = field(default_factory=lambda: f"inst_{uuid4().hex[:12]}")
    #: multi-workspace runtime limits (2.4); disabled by default
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    leases: LeaseManager = field(default_factory=LeaseManager)
    max_workspaces: int = 0
    #: platform-level read-only auditors (2.4): may read every workspace,
    #: never write one
    platform_admins: frozenset[str] = frozenset()

    # ---- governance helpers ----

    def llm_for(self, workspace_id: str):
        """The workspace's own model provider, else the platform default.

        ``llm_config`` is per workspace so one tenant's credential/model
        choice cannot silently drive another tenant's agent.
        """
        row = self.repo.get("llm_config", workspace_id, "llm")
        if row and row.get("provider") == "openai" and row.get("api_key"):
            from ..infrastructure.llm.openai_compat import OpenAICompatProvider
            from ..infrastructure.security import unprotect

            key = unprotect(row.get("api_key") or "")
            if key:
                return OpenAICompatProvider(
                    base_url=row.get("base_url") or "https://api.openai.com/v1",
                    model=row.get("model") or "gpt-4o-mini",
                    api_key=key,
                )
        return self.llm

    def require(self, actor: str, workspace_id: str, role: str = "editor") -> None:
        if role == "viewer" and actor in self.platform_admins:
            return  # cross-workspace read-only access (auditor)
        memberships = [
            Membership.from_dict(m)
            for m in self.repo.list("membership", workspace_id)
        ]
        self.policy.check(memberships, actor, workspace_id, role)

    def require_action(self, actor: str, workspace_id: str, action: str) -> None:
        """Authorize a named action through the action/role matrix."""
        memberships = [
            Membership.from_dict(m)
            for m in self.repo.list("membership", workspace_id)
        ]
        self.policy.check_action(memberships, actor, workspace_id, action)

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

    def transaction(self):
        """Atomic write group: aggregate + outbox event commit together.

        ``with ctx.transaction():`` - a use case must use it whenever a state
        change and its domain event are supposed to be all-or-nothing.
        """
        return self.repo.transaction()

    def must_get(self, kind: str, workspace_id: str, entity_id: str) -> dict:
        data = self.repo.get(kind, workspace_id, entity_id)
        if data is None:
            raise NotFoundError(f"{kind} not found: {entity_id!r}")
        return data

    def save(self, kind: str, data: dict) -> int:
        """Unconditional write; returns the new aggregate version."""
        return self.repo.put(kind, data.get("workspace_id"), data["id"], data)

    def load_versioned(self, kind: str, workspace_id: str, entity_id: str) -> tuple[dict, int]:
        """Read a document together with its aggregate version."""
        got = self.repo.get_versioned(kind, workspace_id, entity_id)
        if got is None:
            raise NotFoundError(f"{kind} not found: {entity_id!r}")
        return got

    def save_versioned(self, kind: str, data: dict, expected_version: int | None) -> int:
        """Optimistic-lock write; stale versions raise ConcurrencyConflictError."""
        return self.repo.put_if_version(
            kind, data.get("workspace_id"), data["id"], data, expected_version
        )

    # ---- runtime access (with schema self-healing) ----

    def gateway_for(self, workspace_id: str, ring: str = "current"):
        """The gateway for one deployment ring (current | canary).

        The deployed Release is the source of truth on restart: the kernel is
        rebuilt from ``deployment/<ring> -> release`` and never from the
        mutable ``schema_payload`` (P0-4).  A canary is a separate kernel
        instance, not a second pointer into the serving runtime (P0-5).
        """
        workspace = self.repo.get("workspace", None, workspace_id)
        runtime_config = (workspace or {}).get("runtime_config", {})
        gateway = self.gateways.for_workspace(workspace_id, runtime_config,
                                              ring=ring)
        if not getattr(gateway, "published", True):
            deployment = self.repo.get("deployment", workspace_id, ring) or {}
            # a pending/failed switch must not serve the uncommitted release;
            # rebuild from the last active base until recovery resolves it
            if deployment.get("status") in ("pending", "installing", "failed"):
                release_id = (deployment.get("base_release_id")
                              or deployment.get("release_id"))
            else:
                release_id = deployment.get("release_id")
            release = (self.repo.get("release", workspace_id, release_id)
                       if release_id else None)
            if release is not None:
                from ..ports.runtime import SchemaPayload

                gateway.publish_schema(
                    SchemaPayload(
                        schema=release["schema"],
                        bindings=release.get("bindings", {}),
                        workflows=release.get("workflows", {}),
                    )
                )
                self._check_release_fingerprint(workspace_id, release)
                self._reattach_agent_runners(workspace_id, gateway)
            else:
                # grandfather workspaces that predate the Release contract:
                # rebuild from the stored payload, but report the drift
                payload = self.repo.get("schema_payload", workspace_id,
                                        workspace_id)
                if payload:
                    from ..ports.runtime import SchemaPayload

                    gateway.publish_schema(
                        SchemaPayload(
                            schema=payload["schema"],
                            bindings=payload.get("bindings", {}),
                            workflows=payload.get("workflows", {}),
                        )
                    )
                    self._reattach_agent_runners(workspace_id, gateway)
                    self._check_schema_fingerprint(workspace_id, payload)
        return gateway

    def _check_release_fingerprint(self, workspace_id: str,
                                   release: dict) -> None:
        """A Release is immutable; if its schema drifted from its recorded
        fingerprint the served runtime is not what operators released."""
        try:
            from .events import EventLog
            from .releases import schema_fingerprint

            expected = release.get("fingerprint")
            if not expected:
                return
            actual = schema_fingerprint(release.get("schema") or {})
            if actual == expected:
                return
            detail = {"expected": expected, "actual": actual,
                      "release_id": release.get("id")}
            self.audit_event("system", workspace_id,
                             "schema.fingerprint_mismatch", workspace_id, detail)
            EventLog(self).emit(
                "schema.fingerprint_mismatch", workspace_id, "system",
                "workspace", workspace_id, payload=detail)
        except Exception:
            return

    def _check_schema_fingerprint(self, workspace_id: str, payload: dict) -> None:
        """Self-healing must notice a schema that drifted from its Release.

        Republishing the stored payload rebuilds a working runtime, but if the
        stored schema no longer hashes to the deployed Release fingerprint the
        deployment is *not* what the operators released - surface it as an
        audited event instead of silently serving drift (plan-v2 2.5).
        """
        try:
            from .events import EventLog
            from .releases import DEPLOYMENT_ID, schema_fingerprint

            deployment = self.repo.get("deployment", workspace_id,
                                       DEPLOYMENT_ID) or {}
            release_id = deployment.get("release_id")
            release = (self.repo.get("release", workspace_id, release_id)
                       if release_id else None)
            expected = (release or {}).get("fingerprint")
            if not expected:
                return
            actual = schema_fingerprint(payload.get("schema") or {})
            if actual == expected:
                return
            detail = {"expected": expected, "actual": actual,
                      "release_id": release_id}
            self.audit_event("system", workspace_id,
                             "schema.fingerprint_mismatch", workspace_id, detail)
            EventLog(self).emit(
                "schema.fingerprint_mismatch", workspace_id, "system",
                "workspace", workspace_id, payload=detail)
        except Exception:
            # observation must never break reads (same rule as the change log)
            return

    def _reattach_agent_runners(self, workspace_id: str, gateway) -> None:
        from .agent_runner import make_agent_runner, make_audited_invoker
        from ..domain.intelligence.model import SemanticIndex
        from ..domain.ontology.model import Ontology

        ontologies = self.repo.list("ontology", workspace_id)
        if not ontologies:
            return
        ontology = Ontology.from_dict(ontologies[0])
        context_text = SemanticIndex.from_ontology(ontology).context_text(ontology)
        for data in self.repo.list("capability", workspace_id):
            impl = data.get("implementation", {})
            if data.get("lifecycle") != "published" or impl.get("kind") != "agent":
                continue
            gateway.register_agent_runner(
                f"agent://{data['id']}",
                make_agent_runner(
                    gateway, lambda: self.llm, context_text,
                    impl.get("goal", ""),
                    function_name=f"{data['target_type']}.{data['key']}",
                    invoker=make_audited_invoker(self, workspace_id, gateway),
                ),
            )
