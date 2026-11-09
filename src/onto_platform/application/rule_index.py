"""Per-evaluation in-memory index for rule candidate pruning (P2.5).

The platform does **not** maintain a persistent secondary index: the SQLite
design-time store and the runtime kernel expose no field-level index, and
building one would have to be kept coherent with every write path.  Instead
:class:`RuleFieldIndex` scans the objects of one type **once per evaluation**
and keeps a ``{field: {value: {object_id}}}`` posting list in process memory.
That is enough to skip the objects a rule can provably not fire on; it is
discarded as soon as the evaluation returns.

Only *positive* top-level predicates are usable for pruning: a leaf (or an
``all`` of leaves) with ``eq``, ``in``, ``contains``, ``starts_with`` or
``ends_with``.  ``not``/``any``/nested logic, ``is_null`` and every negative
operator make the rule non-indexable: ``candidates()`` then returns ``None``
and the caller must evaluate the full object set (soundness first).
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.rule.model import LOGICAL_KEYS, MISSING, SPECIAL_FIELDS, RuleSpec

#: Operators whose truth needs the actual value; only these can use postings.
_INDEXABLE_OPERATORS = frozenset({
    "eq", "in", "contains", "starts_with", "ends_with",
})


def _field_value(obj: dict, field_name: str) -> object:
    """Resolve a property / dotted path with the engine's MISSING semantics."""
    properties = obj.get("properties")
    if not isinstance(properties, dict):
        return MISSING
    if "." not in field_name:
        return properties.get(field_name, MISSING)
    current: object = properties
    for segment in field_name.split("."):
        if not isinstance(current, dict) or segment not in current:
            return MISSING
        current = current[segment]
    return current


def _hashable(value: object) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


class _BuildDescriptor:
    """Allow both ``RuleFieldIndex.build(...)`` and ``index.build(...)``."""

    def __get__(self, instance, owner):
        def build(gateway, object_type, rules):
            index = instance if instance is not None else owner()
            index._build(gateway, str(object_type), list(rules))
            return index
        return build


class RuleFieldIndex:
    """Posting lists for one object type, built for one evaluation."""

    build = _BuildDescriptor()

    def __init__(self) -> None:
        self._object_type = ""
        self._postings: dict[str, dict[object, set[str]]] = {}
        self._rule_conditions: dict[int, list[dict]] = {}
        self._incomplete: set[str] = set()
        self._scanned = 0
        self._rules_scanned = 0
        self._rules_indexed = 0

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_conditions(rule: RuleSpec) -> list[dict] | None:
        """Positive leaf predicates usable for pruning, else ``None``.

        A bare leaf and ``{"all": [leaf, ...]}`` are indexable; everything
        else (``not``/``any``/nested logical nodes, aggregate rules) is not.
        """
        if rule.kind != "constraint" or not isinstance(rule.condition, dict):
            return None
        condition = rule.condition
        if "all" in condition:
            if set(condition) != {"all"}:
                return None
            leaves = condition["all"]
            if not isinstance(leaves, list) or not leaves:
                return None
        elif any(key in condition for key in LOGICAL_KEYS):
            return None
        else:
            leaves = [condition]

        predicates: list[dict] = []
        for leaf in leaves:
            if not isinstance(leaf, dict):
                return None
            if any(key in leaf for key in LOGICAL_KEYS):
                return None
            op = leaf.get("op")
            if op not in _INDEXABLE_OPERATORS:
                return None
            field_name = leaf.get("field")
            if not isinstance(field_name, str) or not field_name.strip():
                return None
            field_name = field_name.strip()
            if field_name in SPECIAL_FIELDS:
                return None
            if any(not segment for segment in field_name.split(".")):
                return None
            value = leaf.get("value")
            if op == "in":
                if not isinstance(value, list):
                    return None
                if any(not _hashable(item) for item in value):
                    return None
            elif op == "eq":
                if not _hashable(value):
                    return None
            elif op in ("starts_with", "ends_with"):
                if not isinstance(value, str):
                    return None
            elif not _hashable(value):  # contains
                return None
            predicates.append({"field": field_name, "op": op, "value": value})
        return predicates or None

    def _build(self, gateway, object_type: str,
               rules: Iterable[RuleSpec]) -> None:
        self._object_type = str(object_type)
        rules = list(rules)
        self._rules_scanned = len(rules)
        indexed: list[tuple[RuleSpec, list[dict]]] = []
        for rule in rules:
            predicates = self._extract_conditions(rule)
            if predicates is not None:
                indexed.append((rule, predicates))
        self._rules_indexed = len(indexed)

        fields = sorted({predicate["field"]
                         for _, predicates in indexed
                         for predicate in predicates})
        objects = gateway.find_objects(self._object_type)
        if isinstance(objects, dict):  # report=True shape, be forgiving
            objects = objects.get("objects") or []
        objects = [obj for obj in (objects or []) if isinstance(obj, dict)]
        self._scanned = len(objects)
        if not fields:
            return

        postings: dict[str, dict[object, set[str]]] = {
            field: {} for field in fields
        }
        for obj in objects:
            object_id = str(obj.get("id") or "")
            if not object_id:
                continue
            for field in fields:
                value = _field_value(obj, field)
                if value is MISSING:
                    continue
                if not _hashable(value):
                    # A list/dict cannot be a posting key.  The field must
                    # fall back to a full scan: skipping it would silently
                    # drop a potential match.
                    self._incomplete.add(field)
                    continue
                postings[field].setdefault(value, set()).add(object_id)

        self._postings = postings
        self._rule_conditions = {id(rule): predicates
                                 for rule, predicates in indexed}

    # ------------------------------------------------------------------
    # querying
    # ------------------------------------------------------------------

    def _matching_ids(self, field: str, op: str, value: object) -> set[str]:
        postings = self._postings.get(field, {})
        if op == "eq":
            return set(postings.get(value, ()))
        if op == "in":
            matched: set[str] = set()
            for item in value:
                matched |= postings.get(item, set())
            return matched
        matched = set()
        for stored, ids in postings.items():
            if not isinstance(stored, str):
                continue
            if op == "contains":
                ok = isinstance(value, str) and value in stored
            elif op == "starts_with":
                ok = stored.startswith(value)
            else:  # ends_with
                ok = stored.endswith(value)
            if ok:
                matched |= ids
        return matched

    def candidates(self, rule: RuleSpec) -> set[str] | None:
        """Object ids that can still match ``rule``; ``None`` means all.

        ``None`` covers rules that are not indexable (negative/logical
        predicates, aggregates) and fields whose values were unhashable, so
        the caller can never prune away a real match.
        """
        predicates = self._rule_conditions.get(id(rule))
        if predicates is None:
            return None
        candidate: set[str] | None = None
        for predicate in predicates:
            field = predicate["field"]
            if field in self._incomplete:
                return None
            ids = self._matching_ids(field, predicate["op"],
                                     predicate["value"])
            candidate = ids if candidate is None else candidate & ids
        return candidate

    def stats(self) -> dict:
        """Diagnostics for the evaluation audit/response."""
        return {
            "scanned": self._scanned,
            "fields": len(self._postings),
            "values": sum(len(values)
                          for values in self._postings.values()),
            "rules_indexed": self._rules_indexed,
            "rules_scanned": self._rules_scanned,
        }


__all__ = ["RuleFieldIndex"]
