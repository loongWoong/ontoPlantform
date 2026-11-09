"""Digital Twin service: live snapshot, relation propagation, counterfactuals.

Boundaries (deliberate, and narrower than the name suggests):

* **Snapshot, not streaming.** A snapshot reads the kernel's current state at
  call time through ``GatewayProvider.for_workspace(...)``.  There is no
  subscription, no telemetry ingest, no change feed and no background
  synchronisation: two calls may legitimately return different state hashes
  if the kernel changed in between.
* **Simulation, not actuation.**  ``simulate`` applies hypothetical property
  values to *copies* of the snapshot nodes and propagates them through
  declared policies.  It never writes an object or relation back to the
  kernel, never emits events and never persists a simulated state.
* **Rule-level, not physics-level.**  Counterfactuals re-evaluate the
  workspace's published rules; there is no physical/engineering model, no
  numeric solver and no process isolation (all work happens in this process).
* **No UI.**  This module is a service layer.  The Smart-UI ``twin3d`` view is
  only a 3-D scene presentation of objects and is intentionally not imported
  or widened here.

Propagation semantics (``TWIN_MODES``): one policy says "when a ``source_type``
object changes, write ``target_field`` on every object reached through the
declared ``relation``":

``copy``
    ``target[target_field] = source[source_field or target_field]`` 鈥?value
    unchanged.
``scale``
    ``target[target_field] = numeric(source[source_field or target_field]) *
    factor``; a non-numeric source value skips the hop (nothing is written).
``boolean``
    ``target[target_field] = bool(source[source_field or target_field])`` with
    the platform's plain truthiness (``None``/``False``/``0``/``""``/``[]``
    are false).
``delta``
    additive: ``target[target_field] = numeric(target[target_field] or 0) +
    numeric(source[source_field or target_field]) * factor``.

The declared ``relation`` decides direction: a policy always reads the source
and writes the target, i.e. propagation follows the relation from its
``source`` type to its ``target`` type.  Validation rejects a ``source_type``
that does not match the relation's source, so a policy cannot silently point
the wrong way.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import deque
from typing import Iterable

from ..domain.ontology.model import Ontology, RelationSpec
from ..domain.rule.engine import evaluate_rules
from ..domain.rule.model import RuleSpec
from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext, published_ontology_row

#: repository kind for twin policies (design-time documents, not runtime state)
TWIN_POLICY_KIND = "twin_policy"

#: closed set of propagation modes; see the module docstring for semantics
TWIN_MODES = ("copy", "scale", "boolean", "delta")

DEFAULT_DEPTH = 1
MAX_DEPTH = 4
MAX_NODES = 500
MAX_SCENARIOS = 20

#: traversal directions over the ontology's declared relations
_DIRECTIONS = ("out", "in", "both")

#: change keys that name object metadata rather than top-level properties
_RESERVED_CHANGE_KEYS = {
    "id", "type", "properties", "workspace_id", "tenant_id",
}

#: capabilities echoed by ``snapshot`` so a caller cannot mistake the response
#: for a streaming twin feed
_SNAPSHOT_CAPABILITIES = {
    "liveState": "kernel-snapshot-at-call",
    "simulation": True,
    "sideEffects": False,
    "processIsolation": False,
}

_MISSING = object()


def _state_hash(nodes: list[dict], edges: list[dict]) -> str:
    """SHA-256 over a canonical JSON rendering of the graph.

    Node/edge lists are already deterministically ordered by the traversal, so
    two snapshots of an unchanged kernel hash the same. ``sort_keys`` keeps
    property maps stable; ``default=str`` tolerates kernel values that are not
    plain JSON (they are rendered, not silently dropped).
    """
    payload = json.dumps(
        {"nodes": nodes, "edges": edges},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_depth(depth: object) -> int:
    if isinstance(depth, bool) or not isinstance(depth, int):
        raise ValidationError("depth must be an integer")
    if depth < 0 or depth > MAX_DEPTH:
        raise ValidationError(
            f"depth must be between 0 and {MAX_DEPTH}, got {depth!r}")
    return depth


def _validate_direction(direction: object) -> str:
    if direction not in _DIRECTIONS:
        raise ValidationError(
            f"direction must be one of {list(_DIRECTIONS)}, got {direction!r}")
    return str(direction)


def _validate_max_nodes(max_nodes: object) -> int:
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int):
        raise ValidationError("max_nodes must be an integer")
    if max_nodes < 1 or max_nodes > MAX_NODES:
        raise ValidationError(
            f"max_nodes must be between 1 and {MAX_NODES}, "
            f"got {max_nodes!r}")
    return max_nodes


def _validate_changes(changes: object) -> dict:
    """Top-level property names only; never ids, types or nested paths."""
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("changes must be a non-empty object")
    cleaned: dict = {}
    for key, value in changes.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(
                "change keys must be non-empty property names")
        name = key.strip()
        if name in _RESERVED_CHANGE_KEYS or name.startswith("$") or "." in name:
            raise ValidationError(
                f"unsupported change field: {key!r}; only top-level "
                "properties may be simulated")
        cleaned[name] = copy.deepcopy(value)
    return cleaned


def _to_number(value: object) -> float | None:
    """Numeric view of a value; ``None`` when it is not a usable number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


class DigitalTwinService:
    """Read-only twin capabilities over one workspace's live kernel state."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------
    # shared plumbing
    # ------------------------------------------------------------------

    def _gateway(self, workspace_id: str):
        return self._ctx.gateways.for_workspace(
            workspace_id, self._ctx.default_runtime_config)

    def _published_ontology(self, workspace_id: str) -> Ontology:
        row = published_ontology_row(self._ctx, workspace_id)
        if row is None:
            raise ValidationError(
                "workspace has no published ontology revision; publish an "
                "ontology before using the digital twin")
        return Ontology.from_dict(row)

    @staticmethod
    def _node(obj: dict) -> dict:
        """The stable, serialisable node projection (no kernel internals)."""
        return {
            "ref": f"{obj.get('type')}/{obj.get('id')}",
            "type": obj.get("type"),
            "id": obj.get("id"),
            "properties": copy.deepcopy(obj.get("properties") or {}),
            "version": obj.get("version", 1),
            "updated_at": obj.get("updated_at"),
        }

    @staticmethod
    def _relations_by_name(ontology: Ontology) -> list[tuple[str, RelationSpec]]:
        return [(name, ontology.relations[name])
                for name in sorted(ontology.relations)]

    def _build_snapshot(
        self,
        workspace_id: str,
        ref: str,
        depth: int,
        direction: str,
        max_nodes: int,
        ontology: Ontology,
    ) -> dict:
        """Deterministic, cycle-safe BFS over the declared relations."""
        gateway = self._gateway(workspace_id)
        root_obj = gateway.get_object(ref)
        if root_obj is None:
            raise NotFoundError(f"object not found: {ref!r}")

        root_node = self._node(root_obj)
        nodes: dict[str, dict] = {root_node["ref"]: root_node}
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str, str]] = set()
        truncated = False

        queue: deque[tuple[str, int]] = deque([(root_node["ref"], 0)])
        relations = self._relations_by_name(ontology)
        while queue:
            current_ref, level = queue.popleft()
            if level >= depth:
                continue
            for relation, _spec in relations:
                moves: list[tuple[str, str]] = []  # (direction, neighbour ref)
                if direction in ("out", "both"):
                    for neighbour in gateway.related(current_ref, relation, "out"):
                        moves.append(("out", str(neighbour)))
                if direction in ("in", "both"):
                    for neighbour in gateway.related(current_ref, relation, "in"):
                        moves.append(("in", str(neighbour)))
                for edge_direction, neighbour in sorted(
                        moves, key=lambda item: (item[0], item[1])):
                    edge_key = (relation, current_ref, neighbour, edge_direction)
                    if edge_direction == "in":
                        edge_key = (relation, neighbour, current_ref, "in")
                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)
                    source_ref, target_ref = (
                        (current_ref, neighbour) if edge_direction == "out"
                        else (neighbour, current_ref))
                    if neighbour in nodes:
                        edges.append({
                            "relation": relation, "source": source_ref,
                            "target": target_ref, "direction": edge_direction,
                        })
                        continue
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    neighbour_obj = gateway.get_object(neighbour)
                    if neighbour_obj is None:
                        continue
                    nodes[neighbour] = self._node(neighbour_obj)
                    edges.append({
                        "relation": relation, "source": source_ref,
                        "target": target_ref, "direction": edge_direction,
                    })
                    queue.append((neighbour, level + 1))

        return {
            "workspace_id": workspace_id,
            "ref": root_node["ref"],
            "depth": depth,
            "direction": direction,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stateHash": _state_hash(list(nodes.values()), edges),
            "generatedAt": self._ctx.clock(),
            "truncated": truncated,
        }

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------

    def snapshot(
        self,
        workspace_id: str,
        actor: str,
        ref: str,
        *,
        depth: int = DEFAULT_DEPTH,
        direction: str = "out",
        max_nodes: int = MAX_NODES,
    ) -> dict:
        """Current-state subgraph around ``ref`` (one kernel read at call time).

        The result is a *snapshot*: nodes are object projections, edges are
        declared-relation hops, and ``stateHash`` fingerprints exactly that
        state.  Nothing is cached or persisted between calls.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        depth = _validate_depth(depth)
        direction = _validate_direction(direction)
        max_nodes = _validate_max_nodes(max_nodes)
        ontology = self._published_ontology(workspace_id)

        result = self._build_snapshot(
            workspace_id, ref, depth, direction, max_nodes, ontology)
        self._ctx.audit_event(
            actor, workspace_id, "twin.snapshot", result["ref"],
            {"depth": depth, "direction": direction,
             "nodes": len(result["nodes"]), "edges": len(result["edges"]),
             "truncated": result["truncated"],
             "stateHash": result["stateHash"]})
        return {**result, "capabilities": dict(_SNAPSHOT_CAPABILITIES)}

    # ------------------------------------------------------------------
    # policies
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_policy(
        ontology: Ontology,
        name: object,
        source_type: object,
        relation: object,
        target_field: object,
        source_field: object,
        mode: object,
        factor: object,
        active: object,
    ) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("policy name must be a non-empty string")
        if not isinstance(source_type, str) or not source_type.strip():
            raise ValidationError("source_type must be a non-empty string")
        if not isinstance(relation, str) or not relation.strip():
            raise ValidationError("relation must be a non-empty string")
        if not isinstance(target_field, str) or not target_field.strip():
            raise ValidationError("target_field must be a non-empty string")
        target_field = target_field.strip()
        if target_field.startswith("$") or target_field in _RESERVED_CHANGE_KEYS:
            raise ValidationError(
                f"target_field {target_field!r} is not a property name")
        if source_field is None:
            source_field = ""
        if not isinstance(source_field, str):
            raise ValidationError("source_field must be a string")
        source_field = source_field.strip()
        if source_field.startswith("$") or (
                source_field and source_field in _RESERVED_CHANGE_KEYS):
            raise ValidationError(
                f"source_field {source_field!r} is not a property name")
        if mode not in TWIN_MODES:
            raise ValidationError(
                f"mode must be one of {list(TWIN_MODES)}, got {mode!r}")
        if isinstance(factor, bool) or not isinstance(factor, (int, float)):
            raise ValidationError("factor must be a number")
        if not isinstance(active, bool):
            raise ValidationError("active must be a boolean")

        relation = relation.strip()
        source_type = source_type.strip()
        spec = ontology.relations.get(relation)
        if spec is None:
            raise ValidationError(
                f"relation {relation!r} is not defined in the published "
                "ontology")
        if source_type != spec.source:
            raise ValidationError(
                f"policy source_type {source_type!r} must be the relation's "
                f"source type {spec.source!r}")
        return {
            "name": name.strip(),
            "source_type": source_type,
            "relation": relation,
            "target_field": target_field,
            "source_field": source_field,
            "mode": mode,
            "factor": float(factor),
            "active": active,
        }

    def set_policy(
        self,
        workspace_id: str,
        actor: str,
        name: str,
        source_type: str,
        relation: str,
        target_field: str,
        *,
        source_field: str = "",
        mode: str = "copy",
        factor: float = 1.0,
        active: bool = True,
    ) -> dict:
        """Create or update (by name) a propagation policy; editor only.

        Upsert keeps the row id and bumps ``version`` 鈥?re-defining a policy
        never leaves two rows with the same name behind.
        """
        self._ctx.require(actor, workspace_id, "editor")
        ontology = self._published_ontology(workspace_id)
        fields = self._validate_policy(
            ontology, name, source_type, relation, target_field, source_field,
            mode, factor, active)

        now = self._ctx.clock()
        existing = [
            row for row in self._ctx.repo.list(TWIN_POLICY_KIND, workspace_id)
            if row.get("name") == fields["name"]
        ]
        if existing:
            row = dict(existing[0])
            row.update(fields)
            row["updated_at"] = now
            row["version"] = int(row.get("version") or 1) + 1
            self._ctx.save(TWIN_POLICY_KIND, row)
            created = False
        else:
            row = {
                "id": self._ctx.id_factory(),
                "workspace_id": workspace_id,
                "kind": TWIN_POLICY_KIND,
                **fields,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            self._ctx.save(TWIN_POLICY_KIND, row)
            created = True

        self._ctx.audit_event(
            actor, workspace_id, "twin.policy_set", row["id"],
            {"name": row["name"], "relation": row["relation"],
             "mode": row["mode"], "version": row["version"],
             "created": created})
        return copy.deepcopy(row)

    def list_policies(self, workspace_id: str, actor: str) -> list[dict]:
        """Policies of one workspace, ordered by ``(name, id)``."""
        self._ctx.require(actor, workspace_id, "viewer")
        rows = [copy.deepcopy(row)
                for row in self._ctx.repo.list(TWIN_POLICY_KIND, workspace_id)]
        rows.sort(key=lambda row: (str(row.get("name") or ""),
                                   str(row.get("id") or "")))
        return rows

    def delete_policy(self, workspace_id: str, actor: str,
                      policy_id: str) -> dict:
        """Delete one policy; deleting a missing policy is a ``NotFoundError``."""
        self._ctx.require(actor, workspace_id, "editor")
        row = self._ctx.repo.get(TWIN_POLICY_KIND, workspace_id, policy_id)
        if row is None:
            raise NotFoundError(f"twin policy not found: {policy_id!r}")
        self._ctx.repo.delete(TWIN_POLICY_KIND, workspace_id, policy_id)
        self._ctx.audit_event(
            actor, workspace_id, "twin.policy_deleted", policy_id,
            {"name": row.get("name"), "relation": row.get("relation")})
        return {"deleted": True, "id": policy_id}

    # ------------------------------------------------------------------
    # simulate
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_mode(policy: dict, source_props: dict,
                    target_props: dict) -> tuple[object, object]:
        """Return ``(old_value, new_value)`` for one policy hop.

        Raises :class:`_SkipHop` (a private sentinel) when the mode cannot
        produce a value (e.g. ``scale``/``delta`` over a non-numeric source).
        """
        source_field = policy.get("source_field") or policy["target_field"]
        target_field = policy["target_field"]
        source_value = source_props.get(source_field, _MISSING)
        old_value = target_props.get(target_field, _MISSING)
        mode = policy.get("mode", "copy")
        factor = policy.get("factor", 1.0)

        if mode == "copy":
            new_value = copy.deepcopy(source_value)
        elif mode == "boolean":
            new_value = bool(source_value) if source_value is not _MISSING \
                else False
        else:  # scale / delta need numbers on both sides
            source_number = _to_number(
                None if source_value is _MISSING else source_value)
            if source_number is None:
                raise _SkipHop()
            if mode == "scale":
                new_value = source_number * float(factor)
            else:
                target_number = _to_number(
                    None if old_value is _MISSING else old_value)
                new_value = (target_number or 0.0) + source_number * float(
                    factor)
        old = None if old_value is _MISSING else copy.deepcopy(old_value)
        return old, new_value

    def _propagate(
        self,
        nodes: list[dict],
        edges: list[dict],
        root_ref: str,
        depth: int,
        policies: list[dict],
    ) -> list[dict]:
        """BFS over policy hops, mutating the node copies in place.

        Cycle-safe: a node is expanded at most once, even if the graph (or a
        monkeypatched gateway in tests) contains a cycle.  Determinism: nodes
        are expanded in BFS order, policies in ``(name, id)`` order and
        neighbours in edge order (both already deterministic).
        """
        by_ref = {node["ref"]: node for node in nodes}
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in edges:
            if edge.get("direction") != "out":
                continue
            adjacency.setdefault(edge["source"], []).append(
                (edge["relation"], edge["target"]))

        ordered_policies = sorted(
            policies, key=lambda policy: (str(policy.get("name") or ""),
                                          str(policy.get("id") or "")))
        applied: list[dict] = []
        visited: set[str] = {root_ref}
        queue: deque[tuple[str, int]] = deque([(root_ref, 0)])
        while queue:
            current_ref, level = queue.popleft()
            if level >= depth:
                continue
            node = by_ref.get(current_ref)
            if node is None:
                continue
            for policy in ordered_policies:
                if policy.get("source_type") != node.get("type"):
                    continue
                for relation, target_ref in adjacency.get(current_ref, []):
                    if relation != policy.get("relation"):
                        continue
                    target = by_ref.get(target_ref)
                    if target is None:
                        continue
                    try:
                        old_value, new_value = self._apply_mode(
                            policy, node["properties"], target["properties"])
                    except _SkipHop:
                        # a hop that cannot produce a value propagates nothing
                        continue
                    if target["properties"].get(
                            policy["target_field"], _MISSING) != new_value:
                        target["properties"][policy["target_field"]] = new_value
                        applied.append({
                            "policy_id": policy.get("id"),
                            "name": policy.get("name"),
                            "relation": policy.get("relation"),
                            "mode": policy.get("mode"),
                            "source": current_ref,
                            "target": target_ref,
                            "field": policy["target_field"],
                            "before": old_value,
                            "after": copy.deepcopy(new_value),
                        })
                    # keep propagating even when the written value is unchanged:
                    # a downstream policy may read a different field.
                    if target_ref not in visited:
                        visited.add(target_ref)
                        queue.append((target_ref, level + 1))
        return applied

    def _rules_by_type(self, workspace_id: str) -> dict[str, list[RuleSpec]]:
        grouped: dict[str, list[RuleSpec]] = {}
        for row in self._ctx.repo.list("rule", workspace_id):
            if row.get("lifecycle") != "published":
                continue
            if not row.get("enabled", True):
                continue
            object_type = row.get("object_type")
            if not isinstance(object_type, str):
                continue
            grouped.setdefault(object_type, []).append(
                RuleSpec.from_dict(row))
        return grouped

    @staticmethod
    def _evaluate(nodes: Iterable[dict],
                  rules_by_type: dict[str, list[RuleSpec]]) -> dict:
        """Evaluate the type's published rules against every node copy."""
        violations: list[dict] = []
        for node in nodes:
            applicable = rules_by_type.get(str(node.get("type")))
            if not applicable:
                continue
            result = evaluate_rules(applicable, node)
            for violation in result.violations:
                violations.append({
                    "rule_id": violation.get("rule_id"),
                    "object_id": violation.get("object_id"),
                    "severity": violation.get("severity"),
                })
        passed = not any(item.get("severity") == "error"
                         for item in violations)
        return {"passed": passed, "violations": violations,
                "total": len(violations)}

    @staticmethod
    def _changed_nodes(before_nodes: list[dict],
                       after_nodes: list[dict]) -> list[dict]:
        before_by_ref = {node["ref"]: node for node in before_nodes}
        changed: list[dict] = []
        for node in after_nodes:
            previous = before_by_ref.get(node["ref"])
            if previous is None:
                continue
            fields: dict[str, dict] = {}
            for field in sorted(set(previous["properties"])
                                | set(node["properties"])):
                old = previous["properties"].get(field, _MISSING)
                new = node["properties"].get(field, _MISSING)
                if old is _MISSING and new is _MISSING:
                    continue
                if old is _MISSING or new is _MISSING or old != new:
                    fields[field] = {
                        "before": None if old is _MISSING else copy.deepcopy(old),
                        "after": None if new is _MISSING else copy.deepcopy(new),
                    }
            if fields:
                changed.append({"ref": node["ref"], "type": node.get("type"),
                                "fields": fields})
        changed.sort(key=lambda item: item["ref"])
        return changed

    def simulate(
        self,
        workspace_id: str,
        actor: str,
        ref: str,
        changes: dict,
        *,
        depth: int = DEFAULT_DEPTH,
        policy_ids: list[str] | None = None,
    ) -> dict:
        """Counterfactual: override properties, propagate policies, diff rules.

        Everything happens on copies.  ``noSideEffects`` (and the kernel value
        checked by the tests) is the contract: the stored object is untouched.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        depth = _validate_depth(depth)
        changes = _validate_changes(changes)
        if policy_ids is not None:
            if not isinstance(policy_ids, (list, tuple)):
                raise ValidationError("policy_ids must be a list of policy ids")
            if any(not isinstance(item, str) or not item.strip()
                   for item in policy_ids):
                raise ValidationError(
                    "policy_ids must contain non-empty policy ids")
            selected = {item.strip() for item in policy_ids}
        else:
            selected = None

        ontology = self._published_ontology(workspace_id)
        before_snapshot = self._build_snapshot(
            workspace_id, ref, depth, "out", MAX_NODES, ontology)

        # work on copies only: the snapshot dicts are the simulation's ABox
        nodes = copy.deepcopy(before_snapshot["nodes"])
        edges = copy.deepcopy(before_snapshot["edges"])
        by_ref = {node["ref"]: node for node in nodes}
        root = by_ref[before_snapshot["ref"]]
        root["properties"].update(changes)

        policies = [
            row for row in self._ctx.repo.list(TWIN_POLICY_KIND, workspace_id)
            if row.get("active", True)
            and (selected is None or row.get("id") in selected)
        ]
        applied = self._propagate(
            nodes, edges, before_snapshot["ref"], depth, policies)

        rules_by_type = self._rules_by_type(workspace_id)
        before_rules = self._evaluate(before_snapshot["nodes"], rules_by_type)
        after_rules = self._evaluate(nodes, rules_by_type)

        def _key(item: dict) -> tuple[str, str]:
            return (str(item.get("rule_id") or ""),
                    str(item.get("object_id") or ""))

        before_keys = {_key(item): item for item in before_rules["violations"]}
        after_keys = {_key(item): item for item in after_rules["violations"]}
        resolved = [before_keys[key] for key in sorted(
            set(before_keys) - set(after_keys))]
        introduced = [after_keys[key] for key in sorted(
            set(after_keys) - set(before_keys))]
        changed = self._changed_nodes(before_snapshot["nodes"], nodes)
        changed_count = sum(len(item["fields"]) for item in changed)

        self._ctx.audit_event(
            actor, workspace_id, "twin.simulate", before_snapshot["ref"],
            {"ref": before_snapshot["ref"], "changes": sorted(changes),
             "depth": depth, "resolved": len(resolved),
             "introduced": len(introduced), "changed": changed_count})

        return {
            "ref": before_snapshot["ref"],
            "object_type": root.get("type"),
            "object_id": root.get("id"),
            "depth": depth,
            "changes": changes,
            "before": before_rules,
            "after": after_rules,
            "total_before": before_rules["total"],
            "total_after": after_rules["total"],
            "resolved": resolved,
            "introduced": introduced,
            "changed": changed,
            "changedCount": changed_count,
            "policiesApplied": applied,
            "stateHashBefore": before_snapshot["stateHash"],
            "stateHashAfter": _state_hash(nodes, edges),
            "noSideEffects": True,
        }

    # ------------------------------------------------------------------
    # scenarios
    # ------------------------------------------------------------------

    def scenarios(
        self,
        workspace_id: str,
        actor: str,
        ref: str,
        scenarios: list[dict],
        *,
        depth: int = DEFAULT_DEPTH,
    ) -> dict:
        """Compare 1..20 counterfactuals under one ranking.

        Ranking is ``(introduced, -resolved, changedCount, name)`` ascending:
        fewer new violations first, then more resolved ones, then the smaller
        intervention, then a stable name tie-break.  ``score`` exposes the same
        preference as ``resolved - introduced`` (higher is better) for callers
        that want a single number; ordering always uses the full tuple.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        depth = _validate_depth(depth)
        if not isinstance(scenarios, (list, tuple)) or not scenarios:
            raise ValidationError("scenarios must be a non-empty list")
        if len(scenarios) > MAX_SCENARIOS:
            raise ValidationError(
                f"at most {MAX_SCENARIOS} scenarios may be compared")
        prepared: list[tuple[str, dict]] = []
        for index, item in enumerate(scenarios):
            if not isinstance(item, dict):
                raise ValidationError(
                    f"scenario #{index} must be an object with name and changes")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValidationError(
                    f"scenario #{index} needs a non-empty name")
            prepared.append(
                (name.strip(), _validate_changes(item.get("changes"))))

        results: list[tuple[tuple, dict]] = []
        for name, changes in prepared:
            simulation = self.simulate(
                workspace_id, actor, ref, changes, depth=depth)
            introduced = len(simulation["introduced"])
            resolved = len(simulation["resolved"])
            changed_count = simulation["changedCount"]
            entry = {
                "name": name,
                "score": resolved - introduced,
                "summary": (f"introduced={introduced}, resolved={resolved}, "
                            f"changedFields={changed_count}"),
                "changedCount": changed_count,
                "ruleDelta": {
                    "before": simulation["total_before"],
                    "after": simulation["total_after"],
                    "introduced": introduced,
                    "resolved": resolved,
                },
            }
            ranking = (introduced, -resolved, changed_count, name)
            results.append((ranking, entry))

        results.sort(key=lambda item: item[0])
        ordered = [entry for _, entry in results]
        best = ordered[0]["name"] if ordered else None
        self._ctx.audit_event(
            actor, workspace_id, "twin.scenarios", ref,
            {"ref": ref, "count": len(ordered), "depth": depth,
             "best": best})
        return {
            "root": ref,
            "scenarios": ordered,
            "best": best,
            "noSideEffects": True,
        }


class _SkipHop(Exception):
    """Internal: a policy hop cannot produce a value (skipped, not an error)."""


__all__ = [
    "DEFAULT_DEPTH",
    "DigitalTwinService",
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_SCENARIOS",
    "TWIN_MODES",
    "TWIN_POLICY_KIND",
]
