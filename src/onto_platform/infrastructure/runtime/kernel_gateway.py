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
Projection, however, must materialize objects.  Since kernel v0.2 the
gateway calls ``OntologyRuntime.apply_delta`` - a single validated
transaction that creates missing objects, merges existing ones, asserts
relation edges and enforces facets/identity/cardinality - so a rejected
delta can never leave partially written state behind (v0.9 fix).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ...errors import RuntimeUnavailableError, ValidationError
from ...ports.runtime import (
    InvocationResult,
    RuntimeGateway,
    SchemaPayload,
    SemanticDelta,
)

#: Cap on concurrently executing capabilities.  A runaway workspace must not
#: be able to starve the API with unbounded worker threads (v0.7).  Execution
#: runs on daemon threads so a hung capability never blocks process exit; the
#: timeout path reports ``unknown`` and the abandoned thread is reclaimed by
#: the OS at shutdown.  Truly killing a runaway execution needs process
#: isolation, which is a Phase-2 hardening item.
_INVOKE_SLOTS = threading.BoundedSemaphore(32)

#: change cursor retention (plan-v2 1.9): once the stream exceeds MAX entries
#: the oldest are compacted down to KEEP.  Clients whose cursor is older than
#: ``oldest_change_seq()`` must resync from scratch.
CHANGE_LOG_MAX = 5000
CHANGE_LOG_KEEP = 2500

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
            from ontology_runtime.core.errors import (
                FunctionExecutionError,
                KernelError,
            )
            from ontology_runtime.core.execution import (
                MUTATION_LINK,
                MUTATION_SET_PROPERTY,
                MUTATION_UNLINK,
                FunctionResult,
                Mutation,
            )
            from ontology_runtime.core.refs import ObjectRef
            from ontology_runtime.core.state import Object, Relation
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
            Relation=Relation,
            FunctionExecutionError=FunctionExecutionError,
            KernelError=KernelError,
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


def _wire_compatible(schema: dict) -> dict:
    """Adapt stored schema payloads to the kernel's canonical wire format.

    Payloads written by older platform versions (and ontologies drafted by
    the LLM) may carry snake_case relation cardinalities such as
    ``one_to_many``; the kernel speaks ``one-to-many`` only and rejects the
    payload at load time.  Normalizing here heals already-published
    workspaces without rewriting their stored documents, so the same
    repository keeps working across a kernel upgrade.
    """
    if not isinstance(schema, dict):
        return schema
    relations = schema.get("relations")
    if not isinstance(relations, dict):
        return schema
    from ...domain.ontology.model import normalize_cardinality

    fixed: dict = {}
    for name, relation in relations.items():
        if isinstance(relation, dict) and isinstance(relation.get("cardinality"), str):
            cardinality = normalize_cardinality(relation["cardinality"])
            if cardinality != relation["cardinality"]:
                relation = {**relation, "cardinality": cardinality}
        fixed[name] = relation
    return {**schema, "relations": fixed}


class KernelRuntimeGateway(RuntimeGateway):
    """One workspace, one kernel runtime."""

    def __init__(self, store_kind: str = "memory", db_path: str | None = None,
                 change_log_path: str | None = None) -> None:
        k = _kernel()
        self._store_kind = store_kind
        self._db_path = db_path
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
        # persistent runtimes persist their change log too: the change stream
        # is the observation channel for UIs/agents (design.txt §29) and must
        # survive restarts just like the semantic state it describes
        self._change_log_path = Path(change_log_path) if change_log_path else None
        if self._change_log_path is not None and self._change_log_path.is_file():
            self._replay_change_log()
        # one lock for every runtime touch: the HTTP layer is multi-threaded
        # while the kernel runtime/store are single-threaded by design.  The
        # lock guards schema swaps and the change log only - a slow capability
        # execution releases it so reads never block on an LLM call (v0.7).
        self._lock = threading.RLock()
        # agent-implemented capabilities register their runner here after
        # publish; the dict is passed by reference so late registrations
        # are visible to the executor
        self._agent_runners: dict[str, object] = {}
        # per-thread executing functions: used to reject recursive capability
        # invocations without serializing independent requests on one lock
        self._local = threading.local()

    # ------------------------------------------------------------------
    # schema publishing (Design Time -> Run Time)
    # ------------------------------------------------------------------

    @property
    def published(self) -> bool:
        return self._runtime is not None

    def publish_schema(self, payload: SchemaPayload) -> None:
        k = _kernel()
        # schema swap is one critical section: no reader may observe a
        # half-installed runtime (v0.9 fix)
        with self._lock:
            self._publish_schema_locked(k, payload)

    def _publish_schema_locked(self, k, payload: SchemaPayload) -> None:
        try:
            schema, catalog = k.parse_schema(_wire_compatible(payload.schema))
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
        # v0.2 temporal metadata: "when / by what / at which version" is
        # part of the canonical object shape (not just properties)
        return {
            "type": obj.ref.type,
            "id": obj.ref.id,
            "properties": dict(obj.properties),
            "version": getattr(obj, "version", 1),
            "updated_at": getattr(obj, "updated_at", None),
            "source": getattr(obj, "source", None),
        }

    def get_object(self, ref: str) -> dict | None:
        with self._lock:
            obj = self._require_runtime().store.get_object(self._ref(ref))
        return self._object_dict(obj) if obj else None

    def find_objects(self, object_type: str, filters: dict | None = None,
                     report: bool = False):
        """``find`` with an optional open-world report.

        ``report=True`` returns ``{"objects": [...], "excluded_missing": n}``
        where ``n`` counts objects of the type that were excluded because a
        filtered property is *absent* (unknown), not false (O-9).
        """
        with self._lock:
            result = self._require_runtime().find(
                object_type, report=report, **(filters or {}))
        if report:
            return {
                "objects": [self._object_dict(o) for o in result["objects"]],
                "excluded_missing": result["excluded_missing"],
            }
        return [self._object_dict(o) for o in result]

    def related(self, ref: str, relation: str, direction: str = "out") -> list[str]:
        with self._lock:
            refs = self._require_runtime().related(self._ref(ref), relation, direction)
        return [str(r) for r in refs]

    def count_objects(self) -> int:
        with self._lock:
            return self._require_runtime().count_objects()

    # ------------------------------------------------------------------
    # capability execution -- never raises (kernel contract)
    # ------------------------------------------------------------------

    def invoke(
        self, function: str, target: str | None = None, args: dict | None = None,
        timeout: float | None = None, actor: str | None = None,
    ) -> InvocationResult:
        """Execute a capability; never raises.

        Execution runs on a bounded worker pool.  With ``timeout`` seconds
        the call returns ``status="unknown"`` when the worker has not
        finished yet - an unknown outcome is retryable and must never be
        rendered as a plain failure (v0.6/v0.7).  The gateway lock is NOT
        held while the capability runs, so reads never block behind a slow
        LLM call (v0.7 lock scope).
        """
        if target is not None:
            try:
                object_ref = self._ref(target)
            except ValidationError as exc:
                return InvocationResult(
                    execution_id="", error=str(exc), status="failed")
        else:
            object_ref = None
        with self._lock:
            try:
                runtime = self._require_runtime()
            except RuntimeUnavailableError as exc:
                return InvocationResult(
                    execution_id="", error=str(exc), status="failed")
        # nested invocations (workflow -> capability) run inline on the same
        # worker: the recursion guard lives on that worker's call stack, and
        # re-submitting would both lose it and consume slots while the parent
        # waits
        if getattr(self._local, "stack", None):
            return self._invoke_blocking(
                runtime, function, object_ref, dict(args or {}), actor=actor)

        done = threading.Event()
        box: dict = {}

        def _run() -> None:
            try:
                box["result"] = self._invoke_blocking(
                    runtime, function, object_ref, dict(args or {}), actor=actor)
            except BaseException as exc:  # pragma: no cover - defensive
                box["error"] = exc
            finally:
                done.set()
                _INVOKE_SLOTS.release()

        _INVOKE_SLOTS.acquire()
        threading.Thread(
            target=_run, name=f"onto-invoke-{function}", daemon=True).start()
        finished = done.wait(timeout) if timeout else done.wait()
        if not finished:
            return InvocationResult(
                execution_id="",
                error=f"execution did not finish within {timeout}s; "
                      f"outcome unknown (retry with the same idempotency key)",
                status="unknown",
            )
        if "error" in box:  # pragma: no cover - defensive
            raise box["error"]
        return box["result"]

    def execution_actor(self) -> str | None:
        """Who invoked the capability currently executing on this worker.

        Nested actions (agent tool calls, workflow steps) are audited under
        the same actor as the outer capability invocation (plan-v2 1.8).
        """
        return getattr(self._local, "actor", None)

    def _invoke_blocking(self, runtime, function, target, args,
                         actor: str | None = None) -> InvocationResult:
        k = _kernel()
        # recursion detection is per worker thread: capabilities may invoke
        # capabilities, and a capability that ends up invoking itself would
        # recurse through the executor forever (agent-kind ones call the LLM
        # at every level), so re-entry on the same call stack is rejected
        # instead of hanging
        with self._lock:
            stack = getattr(self._local, "stack", None)
            if stack is None:
                stack = []
                self._local.stack = stack
            if function in stack:
                return InvocationResult(
                    execution_id="",
                    error=f"recursive capability invocation rejected: {function}",
                    status="failed",
                )
            stack.append(function)
            previous_actor = getattr(self._local, "actor", None)
            if actor is not None:
                self._local.actor = actor
        changes: tuple = ()
        try:
            try:
                result = runtime.invoke(
                    function=function, target=target, args=args)
            except TimeoutError as exc:  # provider/executor timeout
                return InvocationResult(
                    execution_id="", error=str(exc) or "execution timed out",
                    status="unknown")
            except Exception as exc:  # defensive: kernel contract says never
                return InvocationResult(
                    execution_id="", error=f"{type(exc).__name__}: {exc}",
                    status="failed")
            error = None
            if result.error is not None:
                error = f"{type(result.error).__name__}: {result.error}"
            changes = tuple(k.to_jsonable(c) for c in result.changes)
            status = getattr(result, "status", None)
            if status is None:  # kernel < v0.2 has no tri-state yet
                status = "failed" if result.error is not None else "succeeded"
        finally:
            with self._lock:
                try:
                    stack.remove(function)
                except ValueError:  # pragma: no cover - defensive
                    pass
                self._local.actor = previous_actor
        if changes:
            self._record(source="invoke", changes=list(changes),
                         function=function, execution_id=result.execution_id)
        return InvocationResult(
            execution_id=result.execution_id,
            output=k.to_jsonable(result.output),
            changes=changes,
            error=error,
            status=status,
        )

    def delete_objects(self, refs: list[str],
                       source: str = "projection") -> list[dict]:
        """Delete objects (and their relations) in one kernel transaction.

        Deletion is driven by the source of truth (projection v2, 1.6): a
        row that disappeared upstream makes its semantic object disappear,
        with ``unlink`` + ``delete_object`` changes on the change stream.
        """
        k = _kernel()
        with self._lock:
            runtime = self._require_runtime()
            object_refs = [self._ref(ref) for ref in refs]
            changes = runtime.delete(object_refs)
            applied = [k.to_jsonable(change) for change in changes]
            if applied:
                self._record(source=source, changes=applied)
            return applied

    # ------------------------------------------------------------------
    # semantic state write (Projection -> Kernel)
    # ------------------------------------------------------------------

    def apply_delta(self, delta: SemanticDelta,
                    source: str = "projection",
                    must_exist: bool = False) -> list[dict]:
        """Apply a validated semantic delta.  ``source`` labels the audit
        entry: projections default to "projection"; workflow steps running
        inside a capability invocation pass "invoke".

        ``must_exist`` forbids the create path: every upsert must name an
        object that already exists (workflow ``set_property`` is a mutation
        primitive, never an implicit object factory).
        """
        k = _kernel()
        with self._lock:
            if must_exist:
                for upsert in delta.upserts:
                    ref = k.ObjectRef(upsert.type, upsert.id)
                    if self._store.get_object(ref) is None:
                        raise ValidationError(
                            f"set_property target does not exist: {ref}"
                        )
            return self._apply_delta_locked(k, delta, source)

    def _apply_delta_locked(self, k, delta: SemanticDelta,
                            source: str = "projection") -> list[dict]:
        runtime = self._require_runtime()
        schema = runtime.schema

        objects: list = []      # kernel Object (create-or-merge)
        relations: list = []    # kernel Relation (idempotent assert)
        mutations: list = []    # kernel Mutation (unlink only here)

        # ---- validate the platform-facing shape first (fast, clear errors) ----
        from ...domain.values import normalize_datetime

        for upsert in delta.upserts:
            otype = schema.objects.get(upsert.type)
            if otype is None:
                raise ValidationError(f"unknown object type: {upsert.type!r}")
            properties = dict(upsert.properties)
            for prop, value in properties.items():
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
                if pdef.type.kind == "datetime" and isinstance(value, str):
                    # O-7: datetime values are canonical UTC ISO-8601 so
                    # string comparison (filters, change log) is chronological
                    properties[prop] = normalize_datetime(value)
            objects.append(
                k.Object(k.ObjectRef(upsert.type, upsert.id), properties)
            )

        for link in delta.links:
            self._validate_edge(schema, link.relation, link.source, link.target)
            relations.append(
                k.Relation(source=self._ref(link.source),
                           relation=link.relation,
                           target=self._ref(link.target))
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

        # ---- one atomic kernel transaction: constraints are enforced there;
        # creates/merges/links no longer run as two separate store calls
        # (v0.9 fix: a failure can no longer leave seed'd ghosts behind) ----
        delete_changes: list = []
        try:
            if delta.deletes:
                refs = []
                for ref in delta.deletes:
                    parts = str(ref).partition("/")
                    if not parts[0] or not parts[2]:
                        raise ValidationError(
                            f"delete ref must be 'Type/Id', got {ref!r}")
                    refs.append(k.ObjectRef(parts[0], parts[2]))
                # the kernel delete transaction runs under the same gateway
                # lock as the delta apply; a projection replacement therefore
                # never interleaves with another writer
                delete_changes = runtime.delete(refs)
            changes = runtime.apply_delta(
                objects, relations, mutations, source=source
            )
        except k.KernelError as exc:
            raise ValidationError(
                f"kernel rejected semantic delta: {exc}"
            ) from exc
        applied = [k.to_jsonable(change) for change in
                   list(delete_changes) + list(changes)]
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

    def observe_changes(self, after_seq: int = 0, limit: int = 0) -> list[dict]:
        with self._lock:
            entries = [dict(e) for e in self._changes if e["seq"] > after_seq]
        return entries[-limit:] if limit else entries

    def oldest_change_seq(self) -> int:
        with self._lock:
            return int(self._changes[0]["seq"]) if self._changes else 0

    def _maybe_compact_changes(self) -> None:
        """Bound the change log: keep the last CHANGE_LOG_KEEP entries.

        Called with the gateway lock held (from ``_record``).  Readers that
        ask for a cursor older than ``oldest_change_seq()`` must fall back to
        a full resync - the API exposes the boundary so clients can detect it.
        """
        if len(self._changes) <= CHANGE_LOG_MAX:
            return
        self._changes = self._changes[-CHANGE_LOG_KEEP:]
        if self._change_log_path is None:
            return
        try:
            lines = [json.dumps(e, ensure_ascii=False, default=str)
                     for e in self._changes]
            tmp = self._change_log_path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(self._change_log_path)
        except OSError:
            pass  # compaction is best effort; observation never breaks writes

    def _record(self, source: str, changes: list[dict], **extra) -> None:
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "ts": _now(), "source": source, "changes": changes}
            entry.update(extra)
            self._changes.append(entry)
            if self._change_log_path is not None:
                try:
                    self._change_log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._change_log_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                except OSError:
                    pass  # observation must never break the write path
            self._maybe_compact_changes()

    def _replay_change_log(self) -> None:
        try:
            with self._change_log_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn tail write from a crash: skip
                    self._changes.append(entry)
                    self._seq = max(self._seq, int(entry.get("seq", 0)))
        except OSError:
            pass
        # a long-lived change log is replayed already compacted
        if len(self._changes) > CHANGE_LOG_MAX:
            self._maybe_compact_changes()
