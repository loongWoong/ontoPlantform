"""Rule contract: declarative business constraints (P1-Rule).

A Rule is a *declaration*, never code.  It names an object type, a JSON
condition AST and the message/severity reported when that condition holds;
it is validated against the live ontology, reviewed/published, and only then
evaluated by the pure engine in :mod:`engine`.  Evaluation walks a closed,
fixed operator set - user input can never reach ``eval``/``exec``.

Condition AST (pure JSON)
-------------------------
leaf:      ``{"field": "<name>", "op": "<op>", "value": <literal>}``
logical:   ``{"all": [<node>, ...]}`` / ``{"any": [<node>, ...]}`` /
           ``{"not": <node>}``
fields:    ``$id`` (object id), ``$type`` (object type) or a property name
           resolved from ``object["properties"]``.

Supported operators: ``eq``, ``ne``, ``gt``, ``gte``, ``lt``, ``lte``,
``in``, ``not_in``, ``contains``, ``not_contains``, ``is_null``,
``not_null``, ``starts_with``, ``ends_with``.

Missing-field semantics (deliberate, so sparse data never raises)
-----------------------------------------------------------------
A property absent from ``object["properties"]`` is ``MISSING``:

* ``is_null``  -> True for ``MISSING`` or ``None``
* ``not_null`` -> False for ``MISSING`` or ``None``
* every other operator evaluates to False for ``MISSING`` (including ``ne``
  and ``not_in``: "unknown" is not "not equal").  Evaluation must never raise
  on a missing property.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from dataclasses import field as dc_field

#: P1 supports a single rule kind; a general rule language is a later step.
RULE_KINDS: tuple[str, ...] = ("constraint",)

#: Only an ``error`` violation fails an evaluation; warning/info are reported.
SEVERITIES: tuple[str, ...] = ("error", "warning", "info")

RULE_LIFECYCLES: tuple[str, ...] = ("draft", "published", "archived")

#: Closed operator set - the AST cannot express anything else.
OPERATORS: tuple[str, ...] = (
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "is_null",
    "not_null",
    "starts_with",
    "ends_with",
)

LOGICAL_KEYS: tuple[str, ...] = ("all", "any", "not")

#: Meta-fields of a runtime object (never properties of the object type).
SPECIAL_FIELDS: tuple[str, ...] = ("$id", "$type")

#: Guards recursive validation against a pathological (but still JSON) AST.
MAX_CONDITION_DEPTH = 32

_LEAF_KEYS = frozenset({"field", "op", "value"})
_NUMERIC_OPERATORS = frozenset({"gt", "gte", "lt", "lte"})
_LIST_OPERATORS = frozenset({"in", "not_in"})
_STRING_OPERATORS = frozenset({"starts_with", "ends_with"})
_NULL_OPERATORS = frozenset({"is_null", "not_null"})
_CONTAINS_OPERATORS = frozenset({"contains", "not_contains"})


class _Missing:
    """Singleton marker for "the property is not present on this object"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


def _is_number(value: object) -> bool:
    """int/float, but never bool (``True`` is not a business number)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


@dataclass(slots=True)
class RuleSpec:
    """One declared business rule (design-time asset).

    ``condition`` is the *violation* condition: the rule fires when it is
    true.  ``when`` is an optional precondition; when it is false the rule is
    skipped instead of evaluated.  All fields have defaults so a damaged
    storage row can always be loaded and then reported by validation.
    """

    id: str = ""
    workspace_id: str = ""
    name: str = ""
    description: str = ""
    object_type: str = ""
    kind: str = "constraint"
    severity: str = "error"
    condition: dict = field(default_factory=dict)
    when: dict = field(default_factory=dict)
    message: str = ""
    priority: int = 100
    enabled: bool = True
    lifecycle: str = "draft"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description,
            "object_type": self.object_type,
            "kind": self.kind,
            "severity": self.severity,
            "condition": copy.deepcopy(self.condition),
            "when": copy.deepcopy(self.when),
            "message": self.message,
            "priority": self.priority,
            "enabled": self.enabled,
            "lifecycle": self.lifecycle,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RuleSpec":
        """Load a rule; unknown fields are ignored and round-trip is stable."""
        data = data or {}
        raw_version = data.get("version", 1)
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            # keep the damaged value so validate_rule_definition can report it
            version = raw_version
        return cls(
            id=str(data.get("id") or ""),
            workspace_id=str(data.get("workspace_id") or ""),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            object_type=str(data.get("object_type") or ""),
            kind=str(data.get("kind") or "constraint"),
            severity=str(data.get("severity") or "error"),
            condition=copy.deepcopy(data.get("condition") or {}),
            when=copy.deepcopy(data.get("when") or {}),
            message=str(data.get("message") or ""),
            priority=data.get("priority", 100),
            # no bool() coercion: a wrongly typed flag must reach validation
            enabled=data.get("enabled", True),
            lifecycle=str(data.get("lifecycle") or "draft"),
            version=version,
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass(slots=True)
class RuleViolation:
    """One rule firing on one object (a report row, never an exception)."""

    rule_id: str = ""
    rule_name: str = ""
    object_type: str = ""
    object_id: str = ""
    severity: str = "error"
    message: str = ""
    field: str = ""
    # ``field`` above shadows the dataclasses helper in this class body.
    detail: dict = dc_field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "severity": self.severity,
            "message": self.message,
            "field": self.field,
            "detail": copy.deepcopy(self.detail),
        }


@dataclass(slots=True)
class RuleEvaluation:
    """Result of evaluating one or many rules against one object.

    ``passed`` is False only when at least one ``error`` violation was
    recorded; ``warning``/``info`` violations are still listed but do not
    fail the evaluation.  ``skipped`` counts rules not evaluated because
    they are disabled or their ``when`` precondition is false.
    """

    passed: bool = True
    evaluated_rules: int = 0
    violations: list[dict] = field(default_factory=list)
    skipped: int = 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "evaluated_rules": self.evaluated_rules,
            "violations": copy.deepcopy(self.violations),
            "skipped": self.skipped,
        }


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------


def _property_names(object_spec: object) -> set[str]:
    """Property names from an ObjectTypeSpec dataclass or its serialized dict."""
    raw: object = None
    if isinstance(object_spec, dict):
        raw = object_spec.get("properties")
    else:
        raw = getattr(object_spec, "properties", None)
    if isinstance(raw, dict):
        return {str(name) for name in raw}
    names: set[str] = set()
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
            elif isinstance(item, str):
                names.add(item)
    return names


def _validate_node(
    node: object,
    *,
    where: str,
    properties: set[str] | None,
    depth: int,
    problems: list[str],
) -> None:
    if depth > MAX_CONDITION_DEPTH:
        problems.append(
            f"{where}: condition nests deeper than {MAX_CONDITION_DEPTH} levels")
        return
    if not isinstance(node, dict):
        problems.append(f"{where} must be an object")
        return
    if not node:
        problems.append(f"{where} must not be empty")
        return

    logical = [key for key in LOGICAL_KEYS if key in node]
    if logical:
        unknown = sorted(set(node) - set(LOGICAL_KEYS))
        if unknown:
            problems.append(f"{where} has unknown fields {unknown}")
        if len(logical) > 1:
            problems.append(f"{where} mixes logical operators {logical}")
            return
        key = logical[0]
        value = node[key]
        if key == "not":
            _validate_node(value, where=f"{where}.not", properties=properties,
                           depth=depth + 1, problems=problems)
            return
        if not isinstance(value, list):
            problems.append(f"{where}.{key} must be a list of conditions")
            return
        if not value:
            problems.append(f"{where}.{key} must not be empty")
            return
        for index, item in enumerate(value):
            _validate_node(item, where=f"{where}.{key}[{index}]",
                           properties=properties, depth=depth + 1,
                           problems=problems)
        return

    unknown = sorted(set(node) - _LEAF_KEYS)
    if unknown:
        problems.append(
            f"{where} has unknown fields {unknown}; a leaf condition accepts "
            "'field', 'op' and 'value'")

    field_name = node.get("field")
    if not isinstance(field_name, str) or not field_name.strip():
        problems.append(f"{where}.field must be a non-empty property name")
    else:
        field_name = field_name.strip()
        if (properties is not None and field_name not in SPECIAL_FIELDS
                and field_name not in properties):
            problems.append(
                f"{where}.field {field_name!r} is not a property defined in "
                "the current ontology")

    op = node.get("op")
    if not isinstance(op, str) or op not in OPERATORS:
        problems.append(
            f"{where}.op {op!r} is not supported; expected one of "
            f"{list(OPERATORS)}")
        return

    if op in _NULL_OPERATORS:
        if "value" in node and node["value"] is not None:
            problems.append(f"{where}.{op} does not take a value")
        return

    if "value" not in node:
        problems.append(f"{where}.{op} requires a 'value'")
        return

    value = node["value"]
    if op in _LIST_OPERATORS:
        if not isinstance(value, list):
            problems.append(f"{where}.value must be a list for op {op!r}")
    elif op in _NUMERIC_OPERATORS:
        if not _is_number(value):
            problems.append(
                f"{where}.value must be a number for op {op!r}, got "
                f"{value!r}")
    elif op in _STRING_OPERATORS:
        if not isinstance(value, str):
            problems.append(
                f"{where}.value must be a string for op {op!r}, got "
                f"{value!r}")
    elif op in _CONTAINS_OPERATORS:
        if not _is_scalar(value):
            problems.append(
                f"{where}.value must be a JSON scalar for op {op!r}")
    elif not _is_scalar(value):
        # eq / ne
        problems.append(
            f"{where}.value must be a JSON scalar for op {op!r}, got "
            f"{type(value).__name__}")


def validate_condition(
    condition: object,
    *,
    properties: set[str] | None = None,
    where: str = "condition",
) -> list[str]:
    """Structural validation of a condition AST.

    ``properties`` is the set of property names of the target object type;
    ``None`` skips the field-existence check (used at evaluation time where
    an unknown property is simply MISSING, not an error).
    """
    problems: list[str] = []
    _validate_node(condition, where=where, properties=properties, depth=0,
                   problems=problems)
    return problems


def validate_rule_definition(rule: RuleSpec, object_spec: object | None) -> list[str]:
    """Validate one rule against the object type it targets.

    ``object_spec`` is the serialized ``ObjectTypeSpec`` dict (or the
    dataclass itself); ``None`` means the object type is not defined in the
    ontology.  Returns human-readable problems, empty when the rule is valid.
    """
    problems: list[str] = []

    name = rule.name if isinstance(rule.name, str) else ""
    if not name.strip():
        problems.append("rule name must not be empty")

    message = rule.message if isinstance(rule.message, str) else ""
    if not message.strip():
        problems.append("rule message must not be empty")

    object_type = rule.object_type if isinstance(rule.object_type, str) else ""
    if not object_type.strip():
        problems.append("rule object_type must not be empty")

    if rule.kind not in RULE_KINDS:
        problems.append(
            f"rule kind {rule.kind!r} not in {list(RULE_KINDS)}")
    if rule.severity not in SEVERITIES:
        problems.append(
            f"rule severity {rule.severity!r} not in {list(SEVERITIES)}")
    if not isinstance(rule.priority, int) or isinstance(rule.priority, bool):
        problems.append(f"rule priority must be an int, got {rule.priority!r}")
    if rule.lifecycle not in RULE_LIFECYCLES:
        problems.append(
            f"rule lifecycle {rule.lifecycle!r} not in {list(RULE_LIFECYCLES)}")
    if (not isinstance(rule.version, int) or isinstance(rule.version, bool)
            or rule.version < 1):
        problems.append(f"rule version must be a positive int, got {rule.version!r}")
    if not isinstance(rule.enabled, bool):
        problems.append(f"rule enabled must be a boolean, got {rule.enabled!r}")

    properties: set[str] | None = None
    spec_name: str | None = None
    if isinstance(object_spec, dict):
        raw_spec_name = object_spec.get("name")
    else:
        raw_spec_name = getattr(object_spec, "name", None)
    if isinstance(raw_spec_name, str) and raw_spec_name.strip():
        spec_name = raw_spec_name.strip()
    if object_spec is None:
        problems.append(
            f"object type {rule.object_type!r} is not defined in the ontology")
    elif spec_name is not None and object_type.strip() and (
            spec_name != object_type.strip()):
        # a stale reference: the ontology revision no longer carries this type
        problems.append(
            f"object type {rule.object_type!r} is not defined in the ontology")
    else:
        properties = _property_names(object_spec)

    problems.extend(
        validate_condition(rule.condition, properties=properties,
                          where="condition"))

    when = rule.when
    if when:
        if not isinstance(when, dict):
            problems.append("rule when must be an object")
        else:
            problems.extend(
                validate_condition(when, properties=properties, where="when"))
    return problems


__all__ = [
    "LOGICAL_KEYS",
    "MAX_CONDITION_DEPTH",
    "MISSING",
    "OPERATORS",
    "RULE_KINDS",
    "RULE_LIFECYCLES",
    "SEVERITIES",
    "SPECIAL_FIELDS",
    "RuleEvaluation",
    "RuleSpec",
    "RuleViolation",
    "validate_condition",
    "validate_rule_definition",
]
