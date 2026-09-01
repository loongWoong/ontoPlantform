"""KernelRuntimeGateway: the one place that knows both worlds.

Platform side it speaks ``"Type/Id"`` strings, plain dicts and
``InvocationResult``; kernel side it drives the Ontology Runtime Kernel
(schema publish, five APIs, state deltas).  Nothing else in the platform
imports the kernel (design.txt §30/§31/§47 原则三).

Semantic state ownership (design.txt §16/§47 原则一):

    - the kernel store inside this gateway is the ONLY semantic state
    - publish_schema() swaps definitions, never state
    - apply_delta() is the single ingestion door (Projection -> Kernel)

Object creation note: the kernel's public API intentionally has no
"create object" (writes are Function-internal mutation primitives).  A
Projection, however, must materialize objects, so the gateway uses the
store's public bootstrap path (``seed``) for *creates* and the store's
mutation path (``apply``) for property updates and relation edges -
both behind schema validation performed here, first.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from ...errors import RuntimeUnavailableError, ValidationError
from ...ports.runtime import (
    InvocationResult,
    RuntimeGateway,
    SchemaPayload,
    SemanticDelta,
)

_KERNEL: SimpleNamespace | None = None
_KERNEL_LOCK = threading.Lock()


def _kernel() -> SimpleNamespace:
    """Lazy kernel import: the platform core stays kernel-free."""
    global _KERNEL
    with _KERNEL_LOCK:
        if _KERNEL is not None:
            return _KERNEL
        try:
            from ontology_runtime.adapters.serialize import to_jsonable
            from ontology_runtime.core.errors import FunctionExecutionError
            from ontology_runtime.core.execution import (
                MUTATION_LINK,
                MUTATION_SET_PROPERTY,
                MUTATION_UNLINK,
                FunctionResult,
                Mutation,
            )
            from ontology_runtime.core.refs import ObjectRef
            from ontology_runtime.core.state import Object
            from ontology_runtime.infrastructure.bindings import load_bindings
            from ontology_runtime.infrastructure.memory import MemoryStateStore
            from ontology_runtime.infrastructure.python_executor import (
                PythonFunctionExecutor,
            )
            from ontology_runtime.infrastructure.sqlite import SQLiteStateStore
            from ontology_runtime.loader import parse_schema
            from ontology_runtime.runtime.runtime import OntologyRuntime
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeUnavailableError(
                "the Ontology Runtime Kernel is not installed; "
                "run: pip install -e <ontoRK repo>  (or pip install onto-platform[kernel])"
            ) from exc
        _KERNEL = SimpleNamespace(
            to_jsonable=to_jsonable,
            Mutation=Mutation,
            Object=Object,
            ObjectRef=ObjectRef,
            FunctionExecutionError=FunctionExecutionError,
            FunctionResult=FunctionResult,
            MUTATION_LINK=MUTATION_LINK,
            MUTATION_SET_PROPERTY=MUTATION_SET_PROPERTY,
            MUTATION_UNLINK=MUTATION_UNLINK,
            MemoryStateStore=MemoryStateStore,
            SQLiteStateStore=SQLiteStateStore,
            PythonFunctionExecutor=PythonFunctionExecutor,
            load_bindings=load_bindings,
            parse_schema=parse_schema,
            OntologyRuntime=OntologyRuntime,
        )
        return _KERNEL


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KernelRuntimeGateway(RuntimeGateway):
    """One workspace, one kernel runtime."""

    def __init__(self, store_kind: str = "memory", db_path: str | None = None) -> None:
        k = _kernel()
        if store_kind == "sqlite":
            if not db_path:
                raise ValidationError("sqlite runtime store requires db_path")
            self._store = k.SQLiteStateStore(db_path)
        elif store_kind == "memory":
            self._store = k.MemoryStateStore()
        else:
            raise ValidationError(f"unknown runtime store kind: {store_kind!r}")
        self._runtime = None
        self._changes: list[dict] = []
        self._seq = 0
        # one lock for every runtime touch: the HTTP layer is multi-threaded
        # while the kernel runtime/store are single-threaded by design
        self._lock = threading.RLock()
        # agent-implemented capabilities register their runner here after
        # publish; the dict is passed by reference so late registrations
        # are visible to the executor
        self._agent_runners: dict[str, object] = {}

    # ------------------------------------------------------------------
    # schema publishing (Design Time -> Run Time)
    # ------------------------------------------------------------------

    @property
    def published(self) -> bool:
        return self._runtime is not None

    def publish_schema(self, payload: SchemaPayload) -> None:
        k = _kernel()
        try:
            schema, catalog = k.parse_schema(payload.schema)
            bindings = k.load_bindings({"bindings": payload.bindings})
        except Exception as exc:
            raise ValidationError(f"kernel rejected schema payload: {exc}") from exc

        # late-bound executor: python bindings go to the kernel's executor,
        # workflow bindings come back into the platform's runner, agent
        # bindings dispatch to runners registered via register_agent_runner.
        # Kernel types are constructed HERE and injected as factories so the
        # rest of the platform never imports the kernel (design.txt §47 原则三).
        from ..execution.platform_executor import PlatformExecutor
        from ..execution.workflow_runner import GatewayWorkflowRunner

        def execution_error(message: str):
            return k.FunctionExecutionError(message)

        runner = GatewayWorkflowRunner(self)
        executor = PlatformExecutor(
            bindings=bindings,
            workflows=dict(payload.workflows),
            python_executor=k.PythonFunctionExecutor(bindings),
            runner=runner,
            agents=self._agent_runners,
            execution_error=execution_error,
            make_result=lambda execution_id, output=None, error=None: k.FunctionResult(
                execution_id=execution_id, output=output, error=error
            ),
        )
        # state store is preserved across republish: definitions change,
        # semantic state does not (design.txt §9: Schema immutable, State mutable)
        self._runtime = k.OntologyRuntime(
            schema=schema, catalog=catalog, store=self._store, executor=executor
        )

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def _require_runtime(self):
        if self._runtime is None:
            raise RuntimeUnavailableError(
                "no schema published for this workspace yet; "
                "publish an ontology first"
            )
        return self._runtime

    def describe(self) -> dict:
        with self._lock:
            return self._require_runtime().describe()

    def describe_object(self, name: str) -> dict:
        with self._lock:
            return self._require_runtime().describe_object(name)

    def describe_relation(self, name: str) -> dict:
        with self._lock:
            return self._require_runtime().describe_relation(name)

    def describe_function(self, name: str) -> dict:
        with self._lock:
            return self._require_runtime().describe_function(name)

    # ------------------------------------------------------------------
    # semantic state read
    # ------------------------------------------------------------------

    @staticmethod
    def _ref(ref: str):
        k = _kernel()
        if not isinstance(ref, str) or "/" not in ref:
            raise ValidationError(f"object reference must be 'Type/Id', got {ref!r}")
        return k.ObjectRef.parse(ref)

    @staticmethod
    def _object_dict(obj) -> dict:
        return {"type": obj.ref.type, "id": obj.ref.id, "properties": dict(obj.properties)}

    def get_object(self, ref: str) -> dict | None:
        with self._lock:
            obj = self._require_runtime().store.get_object(self._ref(ref))
        return self._object_dict(obj) if obj else None

    def find_objects(self, object_type: str, filters: dict | None = None) -> list[dict]:
        with self._lock:
            objects = self._require_runtime().find(object_type, **(filters or {}))
        return [self._object_dict(o) for o in objects]

    def related(self, ref: str, relation: str, direction: str = "out") -> list[str]:
        with self._lock:
            refs = self._require_runtime().related(self._ref(ref), relation, direction)
        return [str(r) for r in refs]

    # ------------------------------------------------------------------
    # capability execution -- never raises (kernel contract)
    # ------------------------------------------------------------------

    def invoke(
        self, function: str, target: str | None = None, args: dict | None = None
    ) -> InvocationResult:
        k = _kernel()
        with self._lock:
            try:
                runtime = self._require_runtime()
            except RuntimeUnavailableError as exc:
                return InvocationResult(execution_id="", error=str(exc))
            object_ref = None
            if target is not None:
                try:
                    object_ref = self._ref(target)
                except ValidationError as exc:
                    return InvocationResult(execution_id="", error=str(exc))
            result = runtime.invoke(function=function, target=object_ref, args=dict(args or {}))
            error = None
            if result.error is not None:
                error = f"{type(result.error).__name__}: {result.error}"
            changes = tuple(k.to_jsonable(c) for c in result.changes)
        if changes:
            self._record(source="invoke", changes=list(changes), function=function)
        return InvocationResult(
            execution_id=result.execution_id,
            output=k.to_jsonable(result.output),
            changes=changes,
            error=error,
        )

    # ------------------------------------------------------------------
    # semantic state write (Projection -> Kernel)
    # ------------------------------------------------------------------

    def apply_delta(self, delta: SemanticDelta,
                    source: str = "projection") -> list[dict]:
        """Apply a validated semantic delta.  ``source`` labels the audit
        entry: projections default to "projection"; workflow steps running
        inside a capability invocation pass "invoke"."""
        k = _kernel()
        with self._lock:
            return self._apply_delta_locked(k, delta, source)

    def _apply_delta_locked(self, k, delta: SemanticDelta,
                            source: str = "projection") -> list[dict]:
        runtime = self._require_runtime()
        schema = runtime.schema

        creates: list = []      # kernel Object
        mutations: list = []    # kernel Mutation
        applied: list[dict] = []

        # ---- validate everything first (atomicity: validate-before-apply) ----
        for upsert in delta.upserts:
            otype = schema.objects.get(upsert.type)
            if otype is None:
                raise ValidationError(f"unknown object type: {upsert.type!r}")
            for prop, value in upsert.properties.items():
                pdef = otype.properties.get(prop)
                if pdef is None:
                    raise ValidationError(
                        f"{upsert.type} has no property {prop!r}"
                    )
                if not pdef.type.check(value):
                    raise ValidationError(
                        f"property {upsert.type}.{prop} expects {pdef.type.kind}, "
                        f"got {type(value).__name__}"
                    )
            ref = k.ObjectRef(upsert.type, upsert.id)
            existing = self._store.get_object(ref)
            if existing is None:
                creates.append(k.Object(ref, dict(upsert.properties)))
            else:
                for prop, value in upsert.properties.items():
                    if prop in existing.properties and existing.properties[prop] == value:
                        continue  # no-op
                    mutations.append(
                        k.Mutation(
                            kind=k.MUTATION_SET_PROPERTY,
                            object_ref=ref,
                            property=prop,
                            value=value,
                        )
                    )

        for link in delta.links:
            self._validate_edge(schema, link.relation, link.source, link.target)
            mutations.append(
                k.Mutation(
                    kind=k.MUTATION_LINK,
                    source=self._ref(link.source),
                    relation=link.relation,
                    target=self._ref(link.target),
                )
            )
        for unlink in delta.unlinks:
            self._validate_edge(schema, unlink.relation, unlink.source, unlink.target)
            mutations.append(
                k.Mutation(
                    kind=k.MUTATION_UNLINK,
                    source=self._ref(unlink.source),
                    relation=unlink.relation,
                    target=self._ref(unlink.target),
                )
            )

        # ---- apply: creates via bootstrap path, rest via mutations ----
        if creates:
            self._store.seed(creates, [])
            for obj in creates:
                applied.append(
                    {
                        "kind": "create_object",
                        "object": str(obj.ref),
                        "properties": dict(obj.properties),
                    }
                )
        if mutations:
            for change in self._store.apply(mutations):
                applied.append(k.to_jsonable(change))

        if applied:
            self._record(source=source, changes=applied)
        return applied

    def _validate_edge(self, schema, relation: str, source: str, target: str) -> None:
        rdef = schema.relations.get(relation)
        if rdef is None:
            raise ValidationError(f"relation not defined: {relation!r}")
        src_type, _, _ = source.partition("/")
        tgt_type, _, _ = target.partition("/")
        if not src_type or not tgt_type or "/" not in source or "/" not in target:
            raise ValidationError(
                f"relation {relation!r} endpoints must be 'Type/Id', "
                f"got {source!r}, {target!r}"
            )
        if src_type != rdef.source:
            raise ValidationError(
                f"relation {rdef.name} source must be {rdef.source}, got {src_type}"
            )
        if tgt_type != rdef.target:
            raise ValidationError(
                f"relation {rdef.name} target must be {rdef.target}, got {tgt_type}"
            )

    # ------------------------------------------------------------------
    # agent-implemented capabilities (design.txt §19: Agent / AI+Function)
    # ------------------------------------------------------------------

    def register_agent_runner(self, entrypoint: str, runner) -> None:
        """Register the platform-side runner for one agent capability.

        ``runner(target: str | None, args: dict) -> dict`` is called by the
        executor when a function bound to ``agent://...`` is invoked.  The
        registration lives on the gateway (not in the compiled payload) so
        no LLM provider ever needs to be serializable.
        """
        self._agent_runners[entrypoint] = runner

    # ------------------------------------------------------------------
    # change log (Audit 消费 Kernel Change)
    # ------------------------------------------------------------------

    def observe_changes(self, after_seq: int = 0) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._changes if e["seq"] > after_seq]

    def _record(self, source: str, changes: list[dict], **extra) -> None:
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "ts": _now(), "source": source, "changes": changes}
            entry.update(extra)
            self._changes.append(entry)
