"""Capability use cases: define, publish, invoke (design.txt §17-§20)."""

from __future__ import annotations

import threading

from ..domain.capability.model import (
    Capability,
    CapabilityInput,
    Implementation,
    validate_capability,
)
from ..domain.governance.model import transition
from ..domain.ontology.model import Ontology
from ..errors import ExecutionInProgressError, ValidationError
from .common import ApplicationContext
from .execution_ledger import (
    CLAIMED,
    IN_PROGRESS,
    REPLAY,
    UNKNOWN,
    ExecutionLedger,
)
from .execution_runner import ExecutionRunner


def _active_ontology(ctx: ApplicationContext, workspace_id: str) -> dict | None:
    """The ontology revision capabilities are defined against.

    A revision draft wins over its deprecated parent; ties are broken by
    version/id so the choice never depends on storage row order.
    """
    rows = ctx.repo.list("ontology", workspace_id)
    pool = [row for row in rows if row.get("lifecycle") == "draft"]
    if not pool:
        pool = [row for row in rows if row.get("lifecycle") != "deprecated"]
    if not pool:
        return None
    return max(pool, key=lambda row: (
        int(row.get("version") or 0), str(row.get("id") or "")))


def _function_conflict(ctx: ApplicationContext, workspace_id: str,
                       target_type: str, key: str,
                       exclude_id: str | None = None) -> dict | None:
    """The other capability already owning ``{target_type}.{key}``, if any."""
    function = f"{target_type}.{key}"
    for row in ctx.repo.list("capability", workspace_id):
        if row.get("id") == exclude_id:
            continue
        if f"{row.get('target_type')}.{row.get('key')}" == function:
            return row
    return None


class CreateCapability:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(
        self,
        workspace_id: str,
        actor: str,
        key: str,
        name: str,
        target_type: str,
        description: str = "",
        inputs: list[dict] | None = None,
        implementation: dict | None = None,
        contract: dict | None = None,
    ) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        capability = Capability(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            key=key,
            name=name,
            target_type=target_type,
            description=description,
            inputs=[CapabilityInput.from_dict(i) for i in (inputs or [])],
            implementation=Implementation.from_dict(implementation or {"kind": "python"}),
            contract=dict(contract or {}),
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        problems = validate_capability(capability)
        if problems:
            raise ValidationError("capability validation failed: " + "; ".join(problems))
        # the target object type must exist in the ontology being edited
        ontology = _active_ontology(self._ctx, workspace_id)
        if ontology is None or target_type not in (ontology.get("objects") or {}):
            raise ValidationError(
                f"target object type {target_type!r} is not defined in the ontology"
            )
        self._ctx.save("capability", capability.to_dict())
        self._ctx.audit_event(actor, workspace_id, "capability.create", capability.id,
                              {"function": capability.function_name})
        return capability.to_dict()


class UpdateCapability:
    """Edit a draft capability in place.

    Published capabilities are part of the serving runtime contract and stay
    immutable: clone them into a new draft (``CloneCapability``) or delete them
    to evolve.  The whole definition is re-validated, so a partial edit cannot
    leave an invalid capability behind, and ``{target_type}.{key}`` must stay
    unique because it is the kernel function name.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, capability_id: str,
                 key: str | None = None, name: str | None = None,
                 target_type: str | None = None,
                 description: str | None = None,
                 inputs: list[dict] | None = None,
                 implementation: dict | None = None,
                 contract: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data = self._ctx.must_get("capability", workspace_id, capability_id)
        if data.get("lifecycle") != "draft":
            raise ValidationError(
                "only a draft capability can be edited; the capability is "
                f"{data.get('lifecycle')!r} - clone it into a new draft or "
                "delete it instead")
        if key is None and name is None and target_type is None \
                and description is None and inputs is None \
                and implementation is None and contract is None:
            raise ValidationError(
                "nothing to update: provide at least one of key, name, "
                "target_type, description, inputs, implementation or contract")
        capability = Capability.from_dict(data)
        if key is not None:
            capability.key = str(key).strip()
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValidationError("capability name must not be empty")
            capability.name = name
        if target_type is not None:
            capability.target_type = str(target_type).strip()
        if description is not None:
            capability.description = str(description)
        if inputs is not None:
            if not isinstance(inputs, list):
                raise ValidationError("capability inputs must be a list")
            try:
                capability.inputs = [
                    CapabilityInput.from_dict(item) for item in inputs]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(
                    f"invalid capability input: {exc}") from exc
        if implementation is not None:
            if not isinstance(implementation, dict):
                raise ValidationError(
                    "capability implementation must be a JSON object")
            capability.implementation = Implementation.from_dict(implementation)
        if contract is not None:
            if not isinstance(contract, dict):
                raise ValidationError("capability contract must be a JSON object")
            capability.contract = dict(contract)
        problems = validate_capability(capability)
        if problems:
            raise ValidationError(
                "capability validation failed: " + "; ".join(problems))
        ontology = _active_ontology(self._ctx, workspace_id)
        if ontology is None or capability.target_type not in (
                ontology.get("objects") or {}):
            raise ValidationError(
                f"target object type {capability.target_type!r} is not defined "
                "in the ontology")
        conflict = _function_conflict(
            self._ctx, workspace_id, capability.target_type, capability.key,
            exclude_id=capability.id)
        if conflict is not None:
            raise ValidationError(
                f"capability function {capability.function_name!r} already "
                f"exists (capability {conflict.get('id')!r}); choose a "
                "different key or target_type")
        capability.updated_at = self._ctx.clock()
        self._ctx.save("capability", capability.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "capability.update", capability.id,
            {"function": capability.function_name, "key": capability.key,
             "target_type": capability.target_type,
             "name": capability.name})
        return capability.to_dict()


class CloneCapability:
    """Copy a capability into a brand-new draft.

    Cloning is the safe evolution path: the source (possibly published) keeps
    serving while the copy is edited and published under a new function name.
    The new ``{target_type}.{key}`` must not collide with any existing
    capability (including the source), so the kernel never has two functions
    with the same name.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, capability_id: str,
                 key: str | None = None, name: str | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        source = Capability.from_dict(
            self._ctx.must_get("capability", workspace_id, capability_id))
        new_key = str(key).strip() if key is not None else f"{source.key}_v2"
        if not new_key:
            raise ValidationError("clone key must not be empty")
        new_name = str(name).strip() if name is not None else f"{source.name} 副本"
        if not new_name:
            raise ValidationError("clone name must not be empty")
        conflict = _function_conflict(
            self._ctx, workspace_id, source.target_type, new_key)
        if conflict is not None:
            raise ValidationError(
                f"capability function {source.target_type}.{new_key!r} already "
                f"exists (capability {conflict.get('id')!r}); choose a "
                "different key")
        clone = Capability(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            key=new_key,
            name=new_name,
            target_type=source.target_type,
            description=source.description,
            inputs=[CapabilityInput.from_dict(item.to_dict())
                    for item in source.inputs],
            implementation=Implementation.from_dict(
                source.implementation.to_dict()),
            contract=dict(source.contract),
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        problems = validate_capability(clone)
        if problems:
            raise ValidationError(
                "capability validation failed: " + "; ".join(problems))
        self._ctx.save("capability", clone.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "capability.clone", clone.id,
            {"source_id": source.id, "function": clone.function_name,
             "source_function": source.function_name})
        return clone.to_dict()


class PublishCapability:
    def __init__(self, ctx: ApplicationContext, publisher) -> None:
        self._ctx = ctx
        self._publisher = publisher

    def __call__(self, workspace_id: str, capability_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data = self._ctx.must_get("capability", workspace_id, capability_id)
        capability = Capability.from_dict(data)

        problems = validate_capability(capability)
        if problems:
            raise ValidationError("capability validation failed: " + "; ".join(problems))

        # materialize inline python source -> entrypoint file.  Republishing
        # MUST re-materialize: the source may have changed even though an
        # entrypoint path already exists (v0.1 silently served stale code).
        impl = capability.implementation
        if impl.kind == "python" and impl.source:
            entrypoint = self._ctx.code_store.put(
                f"ws{workspace_id}/{capability.function_name}", impl.source
            )
            capability.implementation = Implementation(
                kind="python", source=impl.source, entrypoint=entrypoint,
                steps=impl.steps,
            )

        if capability.lifecycle == "draft":
            capability.lifecycle = transition(capability.lifecycle, "validate")
        capability.lifecycle = transition(capability.lifecycle, "publish")
        capability.updated_at = self._ctx.clock()

        # compile + publish first: a failing compile must leave the
        # capability draft and the runtime untouched (no half-publish)
        candidate = capability.to_dict()
        rows = [
            c for c in self._ctx.repo.list("capability", workspace_id)
            if c.get("id") != capability.id and c.get("lifecycle") == "published"
        ]
        rows.append(candidate)
        describe = self._publisher.publish(
            workspace_id, actor, action="capability.publish",
            capability_overrides=rows,
        )
        from .events import EventLog
        from .releases import cut_release, deploy_release

        with self._ctx.transaction():
            self._ctx.save("capability", candidate)
            release = cut_release(
                self._ctx, workspace_id, actor, note="capability.publish")
            events = EventLog(self._ctx)
            events.emit(
                "capability.published", workspace_id, actor, "capability",
                capability.id, payload={"function": capability.function_name},
            )
            events.emit(
                "release.cut", workspace_id, actor, "release", release["id"],
                payload={"schema_version": release.get("schema_version")},
            )
        deployed = deploy_release(self._ctx, workspace_id, actor, release["id"])
        return {
            "capability": candidate,
            "runtime": describe,
            "release": release,
            "deployment": deployed["deployment"],
        }


class DeleteCapability:
    """Delete a capability and repair every derived reference.

    A draft capability only lives in the design-time store.  A published one
    is part of the runtime contract, so its deletion republishes the remaining
    published capabilities first (the kernel must accept the new schema),
    detaches it from all applications, and cuts a new Release; only then does
    the capability document disappear.
    """

    def __init__(self, ctx: ApplicationContext, publisher) -> None:
        self._ctx = ctx
        self._publisher = publisher

    def _published_ontology(self, workspace_id: str) -> dict:
        # A revision marks the previous published row ``deprecated`` while the
        # new draft is active, so "non-draft" is the serving revision.  The
        # highest such version is what the runtime was last compiled from.
        rows = [
            row for row in self._ctx.repo.list("ontology", workspace_id)
            if row.get("lifecycle") != "draft"
        ]
        if not rows:
            raise ValidationError(
                "the workspace has no published ontology revision to repair")
        return max(rows, key=lambda row: int(row.get("version") or 0))

    def _detach_apps(self, workspace_id: str, capability_id: str) -> list[str]:
        detached: list[str] = []
        for app in self._ctx.repo.list("application", workspace_id):
            attached = list(app.get("capabilities") or [])
            if capability_id not in attached:
                continue
            app["capabilities"] = [item for item in attached
                                   if item != capability_id]
            app["updated_at"] = self._ctx.clock()
            self._ctx.save("application", app)
            detached.append(app.get("name") or app["id"])
        return detached

    def __call__(self, workspace_id: str, capability_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        data = self._ctx.must_get("capability", workspace_id, capability_id)
        function = f"{data.get('target_type')}.{data.get('key')}"

        if data.get("lifecycle") == "published":
            rows = [
                item for item in self._ctx.repo.list("capability", workspace_id)
                if item.get("id") != capability_id
                and item.get("lifecycle") == "published"
            ]
            # The serving contract must be repaired even when the active
            # ontology row is a newer draft: compile from the latest published
            # revision and leave the draft untouched.  A failing compile
            # leaves the capability and runtime exactly as they were.
            published = self._published_ontology(workspace_id)
            describe = self._publisher.publish(
                workspace_id, actor, action="capability.delete",
                ontology=Ontology.from_dict(
                    {**published, "lifecycle": "published"}),
                capability_overrides=rows,
            )
            from .events import EventLog
            from .releases import cut_release, deploy_release

            with self._ctx.transaction():
                self._ctx.repo.delete("capability", workspace_id, capability_id)
                detached = self._detach_apps(workspace_id, capability_id)
                release = cut_release(
                    self._ctx, workspace_id, actor, note="capability.delete",
                    ontology=published)
                events = EventLog(self._ctx)
                events.emit(
                    "capability.deleted", workspace_id, actor, "capability",
                    capability_id, payload={"function": function},
                )
                events.emit(
                    "release.cut", workspace_id, actor, "release", release["id"],
                    payload={"schema_version": release.get("schema_version")},
                )
            deployed = deploy_release(self._ctx, workspace_id, actor, release["id"])
            self._ctx.audit_event(
                actor, workspace_id, "capability.delete", capability_id,
                {"function": function, "detached_from": detached,
                 "release_id": release["id"]})
            return {
                "deleted": True,
                "id": capability_id,
                "function": function,
                "detached_from": detached,
                "runtime": describe,
                "release": release,
                "deployment": deployed["deployment"],
            }

        with self._ctx.transaction():
            self._ctx.repo.delete("capability", workspace_id, capability_id)
            detached = self._detach_apps(workspace_id, capability_id)
        self._ctx.audit_event(
            actor, workspace_id, "capability.delete", capability_id,
            {"function": function, "detached_from": detached, "draft": True})
        return {
            "deleted": True,
            "id": capability_id,
            "function": function,
            "detached_from": detached,
            "draft": True,
        }


class ListCapabilities:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("capability", workspace_id)


class InvokeCapability:
    """The runtime door used by applications, agents and the HTTP API.

    Execution truth is three-valued and claim-based (P0-2):

    * the idempotency key is claimed with a compare-and-set before any side
      effect, so concurrent requests cannot both run the capability;
    * ``unknown`` (timeout / crashed process) is never replayed as success
      and never retried automatically; it is resolved explicitly;
    * the postcondition is judged independently and a violation turns a
      kernel success into a deterministic failure, never ``ok``.

    The execution itself runs on a bounded daemon worker so the API timeout
    can return ``unknown`` without killing the effect (P1-1).
    """

    def __init__(self, ctx: ApplicationContext,
                 runner: ExecutionRunner | None = None) -> None:
        self._ctx = ctx
        self._ledger = ExecutionLedger(ctx)
        self._runner = runner or ExecutionRunner()

    def __call__(self, workspace_id: str, actor: str, function: str,
                 target: str | None = None, args: dict | None = None,
                 idempotency_key: str | None = None,
                 timeout_seconds: float | None = None) -> dict:
        # invoking a capability can change semantic state (bounded writes),
        # so it is an editor action; viewers only read
        self._ctx.require(actor, workspace_id, "editor")
        self._ctx.rate_limiter.check(workspace_id)
        key = (idempotency_key or "").strip() or None
        claim = self._ledger.claim(workspace_id, actor, function, target,
                                   args or {}, key)
        if claim.kind == IN_PROGRESS:
            raise ExecutionInProgressError(
                "execution_in_progress: the same idempotency key is still "
                "running; retry later with the same key")
        if claim.kind == UNKNOWN:
            prior = dict(claim.record.get("result") or {})
            prior.update({
                "status": "unknown",
                "ok": False,
                "retryable": True,
                "contract_failed": False,
                "idempotent_replay": True,
                "execution_record_id": claim.record.get("id"),
                "resolution": claim.record.get("resolution"),
            })
            return prior
        if claim.kind == REPLAY:
            replay = dict(claim.record.get("result") or {})
            replay.setdefault("retryable", replay.get("status") == "unknown")
            replay.setdefault("contract_failed", False)
            replay["idempotent_replay"] = True
            replay["execution_record_id"] = claim.record.get("id")
            return replay
        return self._run_claim(workspace_id, actor, function, target,
                               args or {}, key, timeout_seconds, claim.record)

    def _run_claim(self, workspace_id: str, actor: str, function: str,
                   target: str | None, args: dict, key: str | None,
                   timeout_seconds: float | None, record: dict) -> dict:
        done = threading.Event()
        box: dict = {}

        def job() -> None:
            try:
                lease_key = f"{workspace_id}:{target or function}"
                with self._ctx.leases.hold(lease_key):
                    payload = self._execute(workspace_id, actor, function,
                                            target, args)
                execution_id = payload.get("execution_id") or record["execution_id"]
                verification = payload.get("verification") or {}
                self._ledger.finalize(
                    record, payload, status=payload["status"],
                    verification=verification.get("post", verification) or None,
                    execution_id=execution_id)
                self._publish_execution(workspace_id, actor, function, target,
                                        payload, key)
                box["payload"] = payload
            except BaseException as exc:  # defensive: never lose the claim
                payload = {
                    "execution_id": record["execution_id"],
                    "output": None,
                    "changes": [],
                    "error": f"{type(exc).__name__}: {exc}",
                    "ok": False,
                    "status": "failed",
                }
                try:
                    self._ledger.finalize(record, payload, status="failed")
                    self._publish_execution(workspace_id, actor, function,
                                            target, payload, key)
                finally:
                    box["payload"] = payload
            finally:
                done.set()

        self._runner.run(job)
        if timeout_seconds is not None and timeout_seconds >= 0:
            if not done.wait(timeout_seconds):
                unknown = {
                    "execution_id": record["execution_id"],
                    "output": None,
                    "changes": [],
                    "error": (
                        f"execution did not finish within "
                        f"{timeout_seconds}s; outcome unknown (do not retry "
                        f"blindly; resolve or retry with the same key)"),
                    "ok": False,
                    "status": "unknown",
                    "retryable": True,
                    "contract_failed": False,
                    "execution_record_id": record["id"],
                }
                self._ledger.mark_unknown(record, unknown)
                self._publish_execution(workspace_id, actor, function, target,
                                        unknown, key)
                return unknown
        done.wait()
        return box["payload"]

    def _execute(self, workspace_id: str, actor: str, function: str,
                 target: str | None, args: dict) -> dict:
        from .verifier import CapabilityVerifier

        verifier = CapabilityVerifier(self._ctx)
        pre = verifier.check_preconditions(workspace_id, function, target, args)
        if pre is not None and not pre["ok"]:
            # a violated precondition is a refused execution: the world is
            # not touched and the caller gets a deterministic failure
            return {
                "execution_id": "",
                "output": None,
                "changes": [],
                "error": "precondition failed: " + "; ".join(
                    f"{c['property']} {c['op']} {c['expected']!r} "
                    f"(actual {c['actual']!r})"
                    for c in pre["violated"]),
                "ok": False,
                "status": "failed",
                "retryable": False,
                "contract_failed": False,
                "verification": {"pre": pre},
            }

        before = verifier.snapshot(workspace_id, target)
        gateway = self._ctx.gateway_for(workspace_id)
        # the platform owns the wall-clock timeout on its worker; the kernel
        # call itself must run to completion so an abandoned effect can still
        # be observed and finalized
        result = gateway.invoke(function, target=target, args=args,
                                timeout=None, actor=actor)
        post = verifier.verify_invocation(
            workspace_id, function, target, args, before, result.status)
        status = result.status
        error = result.error
        contract_failed = False
        if post is not None and not post["ok"] and status == "succeeded":
            # the effect happened but the declared postcondition is false:
            # this is a deterministic contract failure, not success (P0-2)
            status = "failed"
            contract_failed = True
            error = "postcondition failed: " + "; ".join(
                f"{c['property']} {c['op']} {c['expected']!r} "
                f"(actual {c['actual']!r})"
                for c in post["violated"])
            post["effect_applied"] = True
        payload = {
            "execution_id": result.execution_id,
            "output": result.output,
            "changes": list(result.changes),
            "error": error,
            "ok": status == "succeeded",
            "status": status,
            "retryable": status == "unknown",
            "contract_failed": contract_failed,
            "verification": {},
        }
        if pre is not None:
            payload["verification"]["pre"] = pre
        if post is not None:
            payload["verification"]["post"] = post
        return payload

    def _publish_execution(self, workspace_id: str, actor: str, function: str,
                           target: str | None, payload: dict,
                           key: str | None) -> None:
        from .events import EventLog

        events = EventLog(self._ctx)
        events.emit(
            "capability.invoked", workspace_id, actor, "capability",
            function, payload={
                "function": function, "target": target,
                "status": payload.get("status"), "ok": payload.get("ok"),
                "changes": len(payload.get("changes") or []),
                "idempotency_key": key,
            },
            execution_id=payload.get("execution_id") or None,
        )
        verification = payload.get("verification")
        if payload.get("contract_failed"):
            events.emit(
                "capability.contract_failed", workspace_id, actor,
                "capability", function,
                payload={"function": function, "target": target,
                         "error": payload.get("error"),
                         "verification": verification},
                execution_id=payload.get("execution_id") or None,
            )
        if verification:
            events.emit(
                "capability.verified", workspace_id, actor, "capability",
                function,
                payload={"ok": (verification.get("post")
                                or verification.get("pre") or {}).get("ok"),
                         **verification},
                execution_id=payload.get("execution_id") or None,
            )
        self._ctx.audit_event(
            actor, workspace_id, "capability.invoke", function,
            {"target": target, "ok": payload.get("ok"),
             "error": payload.get("error"), "status": payload.get("status"),
             "execution_id": payload.get("execution_id"),
             "idempotency_key": key,
             "changes": list(payload.get("changes") or [])},
        )
