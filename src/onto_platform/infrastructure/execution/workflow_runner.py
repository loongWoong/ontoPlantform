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


class GatewayWorkflowRunner:
    """Runs one workflow definition; the gateway is the only dependency."""

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
        for i, step in enumerate(steps):
            alias = step.get("as") or f"step{i + 1}"
            kind = step.get("kind")
            try:
                output = self._execute(kind, step, ctx)
            except WorkflowStepError:
                raise
            except Exception as exc:
                raise WorkflowStepError(f"step {alias} ({kind}) failed: {exc}") from exc
            ctx["steps"][alias] = output
        # convention: a step aliased "output" wins; otherwise the last step
        ctx["output"] = ctx["steps"].get("output", output)
        return ctx

    # ------------------------------------------------------------------

    def _execute(self, kind: str, step: dict, ctx: dict):
        if kind == "invoke":
            result = self._gateway.invoke(
                _resolve(step["function"], ctx),
                target=_resolve(step["target"], ctx) if step.get("target") else None,
                args={k: _resolve(v, ctx) for k, v in (step.get("args") or {}).items()},
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
