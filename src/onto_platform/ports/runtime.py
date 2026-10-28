"""RuntimeGateway Port: the anti-corruption layer between Platform and Kernel.

Design.txt §30/§31:

    Platform  --(RuntimeGateway)-->  Ontology Runtime Kernel

The platform never touches the kernel's store, schema classes or modules
directly - everything goes through this protocol.  The kernel-backed
implementation lives in infrastructure/runtime/kernel_gateway.py.

Object references are plain ``"Type/Id"`` strings on the platform side;
ObjectRef conversion is the gateway's job (§31).

``invoke`` NEVER raises: every failure is reported through
``InvocationResult.error``, mirroring the kernel contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


# ----------------------------------------------------------------------
# DTOs: platform-side, kernel-free
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObjectUpsert:
    """One semantic object to create or update in the kernel."""

    type: str
    id: str
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LinkAssert:
    """One relation edge to assert (idempotent)."""

    relation: str
    source: str  # "Type/Id"
    target: str  # "Type/Id"


@dataclass(frozen=True, slots=True)
class UnlinkAssert:
    """One relation edge to remove (idempotent)."""

    relation: str
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class SemanticDelta:
    """Source State -> Semantic State, expressed as one logical unit.

    Produced by Projections (Data domain) and applied through the
    gateway; the kernel remains the only owner of semantic state.
    """

    upserts: tuple[ObjectUpsert, ...] = ()
    links: tuple[LinkAssert, ...] = ()
    unlinks: tuple[UnlinkAssert, ...] = ()
    #: objects that disappeared from the source of truth; applied in the same
    #: gateway transaction group as the upserts (projection v3 / P1-10)
    deletes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SchemaPayload:
    """A compiled, kernel-facing schema (design.txt §10).

    ``schema``    - dict compatible with the kernel's ``parse_schema``
                    (objects + relations + functions)
    ``bindings``  - dict compatible with the kernel's ``load_bindings``
    ``workflows`` - workflow definitions keyed by binding entrypoint URI
                    (``workflow://<capability id>``); executed by the
                    platform's executor, opaque to the kernel schema
    """

    schema: dict
    bindings: dict = field(default_factory=dict)
    workflows: dict[str, list[dict]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """Standardized capability invocation result (kernel FunctionResult).

    ``status`` is three-valued (K6): ``succeeded`` / ``failed`` /
    ``unknown``.  ``unknown`` means the effect may or may not have
    happened (e.g. a timeout) - callers must not treat it as a failure;
    they retry with the same idempotency key or reconcile via
    ``execution_id``.
    """

    execution_id: str
    output: object = None
    changes: tuple[dict, ...] = ()
    error: str | None = None
    status: str = "succeeded"

    @property
    def ok(self) -> bool:
        """Only a deterministic success is ``ok``.

        ``unknown`` is a deliberately separate third state: the effect may or
        may not have happened, so it must never be rendered or counted as
        success (control-loop report P0-2).
        """
        return self.status == "succeeded"


# ----------------------------------------------------------------------
# Gateway protocols
# ----------------------------------------------------------------------


class RuntimeGateway(Protocol):
    """The platform's single view of the runtime kernel."""

    # -- schema publishing (Design Time -> Run Time) --

    def publish_schema(self, payload: SchemaPayload) -> None:
        """Compile-time entry: install a new schema + bindings.

        Semantic state is preserved across republish; only definitions
        change.  Raises ValidationError on a payload the kernel rejects.
        """
        ...

    # -- observation (Agent 基础观察能力, design.txt §28) --

    def describe(self) -> dict: ...
    def describe_object(self, name: str) -> dict: ...
    def describe_relation(self, name: str) -> dict: ...
    def describe_function(self, name: str) -> dict: ...

    # -- semantic state read --

    def get_object(self, ref: str) -> dict | None:
        """``"Type/Id"`` -> ``{"type","id","properties"}`` or None."""
        ...

    def find_objects(self, object_type: str, filters: dict | None = None,
                     report: bool = False):
        """Filter objects.  With ``report=True`` return
        ``{"objects": [...], "excluded_missing": n}`` (open-world unknown
        count); otherwise a plain list."""
        ...
    def related(self, ref: str, relation: str, direction: str = "out") -> list[str]: ...

    # -- capability execution --

    def invoke(
        self, function: str, target: str | None = None, args: dict | None = None,
        timeout: float | None = None, actor: str | None = None,
    ) -> InvocationResult:
        """Execute a published capability.  Never raises.

        With ``timeout`` seconds an unfinished execution yields
        ``status="unknown"`` (retryable), not a failure.  ``actor`` is the
        identity nested actions (agent tool calls, workflow steps) are
        audited under.
        """
        ...

    # -- semantic state write (Data -> Kernel, design.txt §15) --

    def apply_delta(self, delta: SemanticDelta, source: str = "projection",
                    must_exist: bool = False) -> list[dict]:
        """Apply one SemanticDelta atomically; return applied changes.

        ``must_exist`` rejects upserts of absent objects (mutation door,
        not an implicit create door).
        """
        ...

    def delete_objects(self, refs: list[str],
                       source: str = "projection") -> list[dict]:
        """Delete objects + incident relations atomically (idempotent).

        Used by projection deletion propagation: the source of truth no
        longer contains the row, so the semantic object disappears too.
        Returns the unlink/delete changes; missing refs are skipped.
        """
        ...

    # -- change observation (Audit 消费 Kernel Change, design.txt §29) --

    def observe_changes(self, after_seq: int = 0) -> list[dict]:
        """Change log entries with ``seq > after_seq``, oldest first."""
        ...


class GatewayProvider(Protocol):
    """Workspaces are isolation containers; each owns one runtime."""

    def for_workspace(self, workspace_id: str, runtime_config: dict) -> RuntimeGateway:
        """Return (and cache) the gateway of a workspace."""
        ...
