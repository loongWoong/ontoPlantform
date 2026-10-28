"""Independent verifier + reconciler (plan-v2 1.4 / 1.5).

The verifier is deliberately separate from the capability implementation:
a postcondition is judged by comparing the *observed* runtime state before
and after the execution against the declared contract.  It never mutates the
world - a violation is reported (event + audit + execution record), and the
owning governance process decides what to do about it.

The reconciler compares source-of-truth rows with the semantic runtime:
differences are reported as ``reconciliation.found_differences`` events.
"""

from __future__ import annotations

from typing import Any

from ..domain.capability.model import CONTRACT_OPS
from ..errors import ValidationError
from .common import ApplicationContext
from .events import EventLog


def find_capability(ctx: ApplicationContext, workspace_id: str,
                    function: str) -> dict | None:
    """Resolve the capability behind a kernel function name."""
    for row in ctx.repo.list("capability", workspace_id):
        key = row.get("key")
        target = row.get("target_type")
        if key and target and f"{target}.{key}" == function:
            return row
    return None


def _resolve_expected(cond: dict, before: dict, args: dict) -> Any:
    value = cond.get("value")
    if isinstance(value, dict):
        if "args" in value:
            return args.get(value["args"])
        if "before" in value:
            return before.get(value["before"])
    return value


def evaluate_condition(cond: dict, now: dict, before: dict,
                       args: dict) -> dict:
    """Evaluate one declared condition; returns {'ok','property','op',...}."""
    prop = cond.get("property", "")
    op = cond.get("op")
    actual = now.get(prop)
    previous = before.get(prop)
    expected = None
    try:
        if op == "changed":
            ok = actual != previous
        elif op == "unchanged":
            ok = actual == previous
        elif op == "eq_prev_plus":
            expected = _resolve_expected(cond, before, args)
            ok = (previous is not None and expected is not None
                  and actual == previous + expected)
        elif op == "in":
            expected = _resolve_expected(cond, before, args)
            ok = actual in (expected or [])
        elif op == "eq":
            expected = _resolve_expected(cond, before, args)
            ok = actual == expected
        elif op == "ne":
            expected = _resolve_expected(cond, before, args)
            ok = actual != expected
        elif op in ("gte", "lte", "gt", "lt"):
            expected = _resolve_expected(cond, before, args)
            if actual is None or expected is None:
                ok = False
            elif op == "gte":
                ok = actual >= expected
            elif op == "lte":
                ok = actual <= expected
            elif op == "gt":
                ok = actual > expected
            else:
                ok = actual < expected
        else:  # pragma: no cover - validated at publish time
            raise ValidationError(f"unknown contract operator: {op!r}")
    except TypeError as exc:
        return {"ok": False, "property": prop, "op": op, "actual": actual,
                "expected": expected, "error": str(exc)}
    return {"ok": ok, "property": prop, "op": op,
            "actual": actual, "expected": expected,
            "before": previous}


class CapabilityVerifier:
    """Judges an execution against the capability's declared contract."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def snapshot(self, workspace_id: str, target: str | None) -> dict | None:
        if not target:
            return None
        from ..domain.values import ObjectRef

        ref = ObjectRef.parse(target)
        try:
            obj = self._ctx.gateway_for(workspace_id).get_object(str(ref))
        except Exception:  # target may not exist yet
            return None
        if not obj:
            return None
        return dict(obj.get("properties") or {})

    def check_preconditions(self, workspace_id: str, function: str,
                            target: str | None, args: dict) -> dict | None:
        capability = find_capability(self._ctx, workspace_id, function)
        contract = (capability or {}).get("contract") or {}
        conditions = contract.get("preconditions") or []
        if not conditions:
            return None
        props = self.snapshot(workspace_id, target) or {}
        checks = [evaluate_condition(c, props, {}, args) for c in conditions]
        return {"ok": all(c["ok"] for c in checks), "checks": checks,
                "violated": [c for c in checks if not c["ok"]], "phase": "pre"}

    def verify_invocation(
        self,
        workspace_id: str,
        function: str,
        target: str | None,
        args: dict,
        before: dict | None,
        status: str,
    ) -> dict | None:
        capability = find_capability(self._ctx, workspace_id, function)
        contract = (capability or {}).get("contract") or {}
        conditions = contract.get("postconditions") or []
        if not conditions:
            return None
        if status != "succeeded":
            # postconditions describe a world that should exist after a
            # successful execution; unknown/failed outcomes are judged by
            # reconciliation instead
            return {"ok": False, "skipped": True,
                    "reason": f"execution status is {status!r}",
                    "checks": [], "violated": [], "phase": "post"}
        props = self.snapshot(workspace_id, target) or {}
        checks = [evaluate_condition(c, props, before or {}, args or {})
                  for c in conditions]
        return {"ok": all(c["ok"] for c in checks), "checks": checks,
                "violated": [c for c in checks if not c["ok"]],
                "phase": "post"}


class RuntimeVerifier:
    """Read-only workspace verification: identity, required properties, refs."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def verify_workspace(self, workspace_id: str, actor: str,
                         object_type: str | None = None,
                         sample: int | None = None) -> dict:
        """Check ABox compliance; ``sample`` limits objects per type (1.11).

        A sample is deterministic (type-wise, evenly spaced over the sorted
        ids) so repeated audits of unchanged state check the same objects.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        ontologies = self._ctx.repo.list("ontology", workspace_id)
        if not ontologies:
            raise ValidationError("workspace has no ontology to verify")
        ontology = ontologies[0]
        gateway = self._ctx.gateway_for(workspace_id)
        problems: list[dict] = []
        checked = 0
        sampled = False
        types = ontology.get("objects", {})
        names = [object_type] if object_type else sorted(types)
        for name in names:
            if name not in types:
                raise ValidationError(f"unknown object type: {name!r}")
            definition = types[name]
            identity = definition.get("identity") or []
            prop_defs = definition.get("properties") or {}
            iterable = prop_defs.values() if isinstance(prop_defs, dict) else prop_defs
            required = [p.get("name") for p in iterable if p.get("required")]
            try:
                objects = gateway.find_objects(name, report=True)["objects"]
            except Exception as exc:  # type may not be in the runtime schema yet
                problems.append({"kind": "runtime-unavailable",
                                 "object_type": name, "error": str(exc)})
                continue
            if sample and 0 < sample < len(objects):
                ordered = sorted(objects, key=lambda o: o["id"])
                step = max(1, len(ordered) // int(sample))
                objects = ordered[::step][:int(sample)]
                sampled = True
            seen: dict[str, str] = {}
            for obj in objects:
                checked += 1
                props = obj.get("properties") or {}
                for prop in required:
                    if props.get(prop) in (None, ""):
                        problems.append({"kind": "missing-required",
                                         "object_type": name, "id": obj["id"],
                                         "property": prop})
                if identity:
                    key = "|".join(str(props.get(f)) for f in identity)
                    if key in seen:
                        problems.append({"kind": "duplicate-identity",
                                         "object_type": name, "id": obj["id"],
                                         "duplicate_of": seen[key],
                                         "identity": identity})
                    else:
                        seen[key] = obj["id"]
        result = {"ok": not problems, "checked": checked,
                  "problems": problems, "object_type": object_type,
                  "sampled": sampled, "sample_size": sample}
        if problems:
            EventLog(self._ctx).emit(
                "reconciliation.found_differences", workspace_id, actor,
                "ontology", ontology["id"], payload={"verification": result},
            )
        return result


class Reconciler:
    """Compares datasource rows with the semantic runtime (read-only)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def reconcile(self, workspace_id: str, actor: str,
                  emit: bool = False) -> dict:
        if actor != "system":
            self._ctx.require(actor, workspace_id, "viewer")
        from ..domain.data.model import DataSource, Projection
        from ..infrastructure.datasource.registry import connector_for

        gateway = self._ctx.gateway_for(workspace_id)
        findings: list[dict] = []
        for data in self._ctx.repo.list("projection", workspace_id):
            projection = Projection.from_dict(data)
            source = DataSource.from_dict(self._ctx.must_get(
                "datasource", workspace_id, projection.datasource_id))
            connector = connector_for(source.kind, source.config)
            # ownership: only objects this projection materialized are its
            # responsibility.  A manually created object of the same type is
            # not drift (P0-6).
            runs = [r for r in self._ctx.repo.list("projection_run", workspace_id)
                    if r.get("projection_id") == projection.id
                    and r.get("status") in ("succeeded", "skipped")]
            owned_refs = set((runs[-1].get("object_refs") or []) if runs else [])
            for mapping in projection.object_mappings:
                asset = source.assets.get(mapping.asset)
                if asset is None:
                    continue
                rows = connector.fetch(asset.config)
                # apply the same row prefilter / missing-id policy as the
                # projection: a filtered-out or id-less row is not expected
                # in the runtime, so it must not look like drift (P1-6)
                rows = [
                    row for row in rows
                    if all(row.get(k) == v for k, v in mapping.filter.items())
                ]
                source_ids = {
                    str(row.get(mapping.id_field))
                    for row in rows if row.get(mapping.id_field) not in (None, "")
                }
                runtime = gateway.find_objects(mapping.object_type, report=True)["objects"]
                runtime_ids = {str(obj["id"]) for obj in runtime}
                missing_in_runtime = sorted(source_ids - runtime_ids)
                owned_ids = {
                    ref.partition("/")[2] for ref in owned_refs
                    if ref.partition("/")[0] == mapping.object_type
                }
                if projection.mode == "authoritative":
                    # this projection is the only writer for the mapped type:
                    # any runtime object without a source row is an orphan
                    extra_ids = runtime_ids - source_ids
                else:
                    # additive mode: extras are only drift when this
                    # projection owned them and they still exist in the runtime
                    extra_ids = (owned_ids & runtime_ids) - source_ids
                missing_in_source = sorted(extra_ids)
                if missing_in_runtime or missing_in_source:
                    findings.append({
                        "kind": "projection-drift",
                        "projection_id": projection.id,
                        "object_type": mapping.object_type,
                        "missing_in_runtime": missing_in_runtime,
                        "missing_in_source": missing_in_source,
                        "ownership": projection.mode,
                    })
        result = {"ok": not findings, "findings": findings}
        if emit:
            EventLog(self._ctx).emit(
                "reconciliation.found_differences", workspace_id, actor,
                "reconciliation", workspace_id, payload=result,
            )
        return result
