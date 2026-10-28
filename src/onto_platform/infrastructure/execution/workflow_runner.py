"""GatewayWorkflowRunner: executes capability workflows against the gateway.

A workflow is an ordered list of steps; every state change goes through
``invoke`` (kernel functions commit atomically per invocation), while
``find`` / ``related`` / ``get`` / ``count`` / ``set`` are observation
and context steps.  Values may reference earlier results with
``{{path.to.value}}`` templates, e.g.::

    {"kind": "invoke", "function": "Vehicle.recharge",
     "target": "{{target}}", "args": {"amount": "{{args.amount}}"},
     "as": "recharge"}
"""

from __future__ import annotations

import re

from ...errors import PlatformError

_TEMPLATE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_ROOTS = ("args", "target", "steps", "vars")


class WorkflowStepError(PlatformError):
    """A workflow step failed; the executor turns this into result.error."""


class WorkflowSagaError(WorkflowStepError):
    """A step failed and the completed steps were compensated.

    ``compensations`` records what ran (or failed to run) so the caller sees
    the true end state instead of a bare "step failed" (plan-v2 1.7).
    """

    def __init__(self, message: str, *, compensations: list[dict]) -> None:
        super().__init__(message)
        self.compensations = compensations


class GatewayWorkflowRunner:
    """Runs one workflow definition; the gateway is the only dependency.

    Saga semantics: every step may declare ``compensate`` (itself a step
    spec).  When a later step fails, completed steps are compensated in
    reverse order; compensation failures are reported, never silently
    swallowed.  A step that returns ``unknown`` is treated as a saga failure
    (it may have happened), so compensation runs instead of assuming success.
    """

    def __init__(self, gateway) -> None:
        self._gateway = gateway

    def run(self, steps: list[dict], context: dict) -> dict:
        ctx: dict = {
            "args": dict(context.get("args") or {}),
            "target": context.get("target"),
            "steps": {},
            "vars": {},
        }
        output = None
        completed: list[tuple[str, dict]] = []
        for i, step in enumerate(steps):
            alias = step.get("as") or f"step{i + 1}"
            kind = step.get("kind")
            try:
                output = self._execute(kind, step, ctx)
            except WorkflowStepError as exc:
                raise self._compensate(completed, ctx, exc) from exc
            except Exception as exc:
                failure = WorkflowStepError(
                    f"step {alias} ({kind}) failed: {exc}")
                raise self._compensate(completed, ctx, failure) from exc
            ctx["steps"][alias] = output
            completed.append((alias, step))
        # convention: a step aliased "output" wins; otherwise the last step
        ctx["output"] = ctx["steps"].get("output", output)
        return ctx

    def _compensate(self, completed: list[tuple[str, dict]], ctx: dict,
                    failure: WorkflowStepError) -> WorkflowStepError:
        """Run declared compensations in reverse order; never raise from here."""
        compensations: list[dict] = []
        for alias, step in reversed(completed):
            spec = step.get("compensate")
            if not spec:
                continue
            before = dict(ctx["steps"])
            try:
                self._execute(spec.get("kind"), spec, ctx)
            except Exception as exc:  # compensation must not mask the original
                compensations.append(
                    {"step": alias, "ok": False, "error": str(exc)})
            else:
                compensations.append({"step": alias, "ok": True})
            finally:
                # every compensation sees the pre-compensation context
                ctx["steps"] = before
        if not compensations:
            return failure
        failed = [c for c in compensations if not c["ok"]]
        detail = ", ".join(
            f"{c['step']}:{'ok' if c['ok'] else 'failed'}" for c in compensations)
        message = f"{failure}; compensated [{detail}]"
        if failed:
            message += f"; COMPENSATION FAILURES: {failed}"
        return WorkflowSagaError(message, compensations=compensations)

    # ------------------------------------------------------------------

    def _execute(self, kind: str, step: dict, ctx: dict):
        if kind == "invoke":
            result = self._gateway.invoke(
                _resolve(step["function"], ctx),
                target=_resolve(step["target"], ctx) if step.get("target") else None,
                args={k: _resolve(v, ctx) for k, v in (step.get("args") or {}).items()},
                timeout=step.get("timeout"),
            )
            if result.status == "unknown":
                raise WorkflowStepError(
                    f"invoke {step['function']!r} outcome unknown: {result.error}"
                )
            if not result.ok:
                raise WorkflowStepError(
                    f"invoke {step['function']!r} failed: {result.error}"
                )
            return result.output

        if kind == "find":
            return self._gateway.find_objects(
                _resolve(step["object_type"], ctx),
                {k: _resolve(v, ctx) for k, v in (step.get("filter") or {}).items()},
            )

        if kind == "related":
            return self._gateway.related(
                _resolve(step["ref"], ctx),
                _resolve(step["relation"], ctx),
                direction=step.get("direction", "out"),
            )

        if kind == "get":
            return self._gateway.get_object(_resolve(step["ref"], ctx))

        if kind == "count":
            value = _resolve(step["list"], ctx)
            if not isinstance(value, list):
                raise WorkflowStepError("'count' expects a list value")
            return len(value)

        if kind == "set":
            value = _resolve(step.get("value"), ctx)
            ctx["vars"][str(step["name"])] = value
            return value

        if kind == "set_property":
            # the one bounded mutation primitive: a workflow may write
            # properties of an existing object through the gateway's
            # validated delta door (design.txt §49: Action -> Change -> State)
            ref = _resolve(step["ref"], ctx)
            obj_type, _, obj_id = str(ref).partition("/")
            if not obj_id:
                raise WorkflowStepError(f"set_property needs a 'Type/Id' ref, got {ref!r}")
            properties = {
                str(k): _resolve(v, ctx)
                for k, v in (step.get("properties") or {}).items()
            }
            if not properties:
                raise WorkflowStepError("set_property needs a non-empty 'properties' mapping")
            from ...ports.runtime import ObjectUpsert, SemanticDelta

            self._gateway.apply_delta(SemanticDelta(
                upserts=(ObjectUpsert(type=obj_type, id=obj_id, properties=properties),),
            ), source="invoke", must_exist=True)
            return dict(properties)

        raise WorkflowStepError(f"unknown step kind {kind!r}")  # pragma: no cover


# ----------------------------------------------------------------------
# template resolution
# ----------------------------------------------------------------------


def _lookup(path: str, ctx: dict):
    parts = [p for p in path.split(".") if p]
    if not parts or parts[0] not in _ROOTS:
        raise WorkflowStepError(
            f"template path {path!r} must start with one of {list(_ROOTS)}"
        )
    node = ctx
    for part in parts:
        if isinstance(node, list):
            try:
                node = node[int(part)]
                continue
            except (ValueError, IndexError):
                raise WorkflowStepError(f"template path {path!r} not found") from None
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise WorkflowStepError(f"template path {path!r} not found")
    return node


def _resolve(value, ctx: dict):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1:
            return _lookup(stripped[2:-2], ctx)
        return _TEMPLATE.sub(lambda m: str(_lookup(m.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: _resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, ctx) for v in value]
    return value
