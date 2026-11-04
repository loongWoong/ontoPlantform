"""Deterministic scope / row-filter language (design §7).

Policies are data, so a data scope is an AST rather than Python code.  The
same AST is evaluated by the PDP (in memory) and compiled to a parameterised
SQL predicate for repositories, which keeps "what the console previewed" and
"what the database applied" the same object.

AST forms::

    {"field": "tenant_id", "op": "eq", "valueFrom": "context.tenant_id"}
    {"field": "station_id", "op": "in", "valueFrom": "principal.attributes.stations"}
    {"all": [<node>, ...]}
    {"any": [<node>, ...]}
    {"not": <node>}

Only allow-listed operators and identifier-shaped field names are accepted;
``valueFrom`` can read the principal/context/resource, never arbitrary code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ...errors import ValidationError

#: Fields may be dotted (``owner.team``) but every component is identifier-like.
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
ALLOWED_OPS = frozenset({
    "eq", "ne", "in", "not_in", "prefix", "contains",
    "gt", "gte", "lt", "lte", "exists", "not_exists",
})
LOGICAL_KEYS = ("all", "any", "not")
NODE_KEYS = frozenset({"field", "op", "value", "valueFrom", *LOGICAL_KEYS})

#: A scope must constrain the tenant boundary; otherwise it is a cross-tenant
#: read waiting to happen.  Workspace is the tenant boundary in V0.1.
TENANT_FIELDS = frozenset({"tenant_id", "workspace_id"})


class _Missing:
    """Sentinel: the referenced attribute does not exist.

    Missing data denies; treating it as ``None`` would make ``ne`` true for a
    field an attacker cannot see, which is exactly the wrong default.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


MISSING = _Missing()


def _validate_ref(ref: str, *, what: str) -> str:
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise ValidationError(f"{what} must be an identifier path, got {ref!r}")
    return ref


def normalize_filter(node: Mapping[str, Any] | None, *,
                     require_tenant: bool = False) -> dict:
    """Validate and return a plain-dict copy of ``node``.

    ``require_tenant`` is used for policy scopes.  A scope that does not
    mention ``tenant_id`` or ``workspace_id`` is rejected at publish time,
    not silently trusted at request time.
    """
    if not isinstance(node, Mapping):
        raise ValidationError("filter must be an object")
    unknown = set(node) - NODE_KEYS
    if unknown:
        raise ValidationError(f"unknown filter keys: {sorted(unknown)}")

    logical = [key for key in LOGICAL_KEYS if key in node]
    if len(logical) > 1:
        raise ValidationError("a filter node may use only one of all/any/not")
    if logical:
        key = logical[0]
        if key == "not":
            if set(node) != {"not"}:
                raise ValidationError("'not' must be the only key of its node")
            child = normalize_filter(node["not"], require_tenant=False)
            if require_tenant and not _mentions_tenant(child):
                raise ValidationError(
                    "a negated scope must still constrain tenant_id or "
                    "workspace_id")
            return {"not": child}
        values = node[key]
        if not isinstance(values, list) or not values:
            raise ValidationError(f"'{key}' must be a non-empty list")
        children = [normalize_filter(child, require_tenant=False)
                    for child in values]
        if require_tenant and key == "all" and not any(_mentions_tenant(c)
                                                       for c in children):
            raise ValidationError(
                "scope must constrain tenant_id or workspace_id")
        if require_tenant and key == "any" and not all(_mentions_tenant(c)
                                                       for c in children):
            # Every branch of a disjunction must be tenant-safe, otherwise the
            # unscoped branch would leak rows from another tenant.
            raise ValidationError(
                "every 'any' branch of a scope must constrain tenant_id or "
                "workspace_id")
        return {key: children}

    if "field" not in node or "op" not in node:
        raise ValidationError("leaf filter needs 'field' and 'op'")
    field = _validate_ref(str(node["field"]), what="filter field")
    op = node["op"]
    if op not in ALLOWED_OPS:
        raise ValidationError(f"unsupported filter operator {op!r}")
    if "value" in node and "valueFrom" in node:
        raise ValidationError("filter leaf has both value and valueFrom")
    if "value" not in node and "valueFrom" not in node:
        raise ValidationError("filter leaf needs value or valueFrom")
    out: dict[str, Any] = {"field": field, "op": op}
    if "value" in node:
        value = node["value"]
        _validate_literal(value, field, op)
        out["value"] = value
    else:
        out["valueFrom"] = _validate_ref(str(node["valueFrom"]),
                                         what="valueFrom")
    if op in ("in", "not_in"):
        source = out.get("value", out.get("valueFrom"))
        if isinstance(source, (str, bytes, Mapping)) and "value" in out:
            raise ValidationError(f"'{op}' needs a list value")
    if require_tenant and field not in TENANT_FIELDS:
        # A leaf alone may not be the whole scope, but nested all/any checks
        # above cover the useful shape.  A bare leaf scope is rejected.
        raise ValidationError("a bare scope leaf must constrain tenant_id "
                              "or workspace_id; wrap it in 'all'")
    return out


def _validate_literal(value: Any, field: str, op: str) -> None:
    if isinstance(value, (dict, list, tuple, set)):
        if op not in ("in", "not_in"):
            raise ValidationError(
                f"operator {op!r} on {field!r} does not accept a container")
        if not isinstance(value, (list, tuple)) or not value:
            raise ValidationError(f"'{op}' value must be a non-empty list")
        for item in value:
            if isinstance(item, (dict, list, tuple, set)):
                raise ValidationError("'in' values must be scalars")
    elif isinstance(value, (bytes, bytearray)):
        raise ValidationError("filter values must be JSON scalars")


def _mentions_tenant(node: Mapping[str, Any]) -> bool:
    if "field" in node:
        return node.get("field") in TENANT_FIELDS
    if "not" in node:
        return _mentions_tenant(node["not"])
    for key in ("all", "any"):
        if key in node:
            return any(_mentions_tenant(child) for child in node[key])
    return False


# ----------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------

def _lookup(record: Mapping[str, Any], field: str) -> Any:
    current: Any = record
    for part in field.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


def _context_value(name: str, principal: Any, context: Any,
                   resource: Mapping[str, Any]) -> Any:
    """Resolve ``valueFrom``.

    Supported roots: ``principal``, ``context``, ``resource`` and ``literal``.
    Anything else is a validation error at evaluation time (which the PDP turns
    into a deny, never an allow).
    """
    root, _, rest = name.partition(".")
    if root == "principal":
        if not rest:
            return principal
        if rest == "id":
            return getattr(principal, "id", MISSING)
        if rest == "tenant_id":
            return getattr(principal, "tenant_id", MISSING)
        if rest == "workspace_id":
            return getattr(principal, "workspace_id", MISSING)
        if rest == "roles":
            return list(getattr(principal, "roles", ()) or ())
        if rest.startswith("attributes."):
            attrs = getattr(principal, "attributes", {}) or {}
            return _lookup(attrs, rest[len("attributes."):])
        return MISSING
    if root == "context":
        if not rest:
            return context
        return _lookup(context, rest)
    if root == "resource":
        if not rest:
            return resource
        return _lookup(resource, rest)
    if root == "literal":
        return rest
    raise ValidationError(f"unsupported valueFrom root {root!r}")


def evaluate_filter(node: Mapping[str, Any], principal: Any, context: Any,
                    resource: Mapping[str, Any]) -> bool:
    """Evaluate a normalized filter.  Missing data always yields ``False``."""
    if "all" in node:
        return all(evaluate_filter(child, principal, context, resource)
                   for child in node["all"])
    if "any" in node:
        return any(evaluate_filter(child, principal, context, resource)
                   for child in node["any"])
    if "not" in node:
        return not evaluate_filter(node["not"], principal, context, resource)

    left = _lookup(resource, node["field"])
    if left is MISSING:
        return node["op"] == "not_exists"
    if "value" in node:
        right = node["value"]
    else:
        try:
            right = _context_value(node["valueFrom"], principal, context,
                                   resource)
        except ValidationError:
            return False
    if right is MISSING:
        return node["op"] == "not_exists"

    op = node["op"]
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "in":
        return isinstance(right, (list, tuple, set)) and left in right
    if op == "not_in":
        return isinstance(right, (list, tuple, set)) and left not in right
    if op == "prefix":
        return isinstance(left, str) and isinstance(right, str) \
            and left.startswith(right)
    if op == "contains":
        return (isinstance(left, str) and isinstance(right, str)
                and right in left) or \
            (isinstance(left, (list, tuple, set)) and right in left)
    if op == "exists":
        return left is not MISSING and left is not None
    if op == "not_exists":
        return left is MISSING or left is None
    if op in ("gt", "gte", "lt", "lte"):
        try:
            if op == "gt":
                return left > right
            if op == "gte":
                return left >= right
            if op == "lt":
                return left < right
            return left <= right
        except TypeError:
            return False
    return False  # pragma: no cover - normalize_filter blocks this


# ----------------------------------------------------------------------
# SQL compilation
# ----------------------------------------------------------------------

_SQL_OPS = {
    "eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
}


def compile_filter_sql(node: Mapping[str, Any], principal: Any, context: Any,
                       *, table_alias: str = "",
                       columns: Mapping[str, str] | None = None,
                       json_data_column: str | None = None) -> tuple[str, list]:
    """Compile a normalized filter into ``(sql, params)``.

    Identifiers are validated by ``normalize_filter`` and optionally mapped
    through ``columns``; values are always parameters.  Set
    ``json_data_column`` to compile non-column fields as
    ``json_extract(<column>, '$.field')`` (SQLite pushdown).  Callers must
    still run the SQL inside the tenant-scoped repository session.
    """
    params: list = []
    sql = _compile(node, principal, context, table_alias, columns, params,
                   json_data_column)
    return sql, params


def _sql_identifier(name: str, table_alias: str,
                    columns: Mapping[str, str] | None,
                    json_data_column: str | None = None) -> str:
    if columns is not None and name in columns:
        mapped = columns[name]
        if not _FIELD_RE.match(mapped):
            raise ValidationError(f"unsafe SQL column mapping for {name!r}")
        return mapped
    if not _FIELD_RE.match(name):
        raise ValidationError(f"unsafe SQL field {name!r}")
    if json_data_column:
        if not _FIELD_RE.match(json_data_column):
            raise ValidationError("unsafe JSON data column")
        return f"json_extract({json_data_column}, '$.{name}')"
    return f"{table_alias}{name}" if table_alias else name


def _compile(node: Mapping[str, Any], principal: Any, context: Any,
             table_alias: str, columns: Mapping[str, str] | None,
             params: list, json_data_column: str | None = None) -> str:
    if "all" in node:
        parts = [_compile(child, principal, context, table_alias, columns,
                          params, json_data_column) for child in node["all"]]
        return "(" + " AND ".join(parts) + ")"
    if "any" in node:
        parts = [_compile(child, principal, context, table_alias, columns,
                          params, json_data_column) for child in node["any"]]
        return "(" + " OR ".join(parts) + ")"
    if "not" in node:
        return "(NOT " + _compile(node["not"], principal, context,
                                  table_alias, columns, params,
                                  json_data_column) + ")"

    field = _sql_identifier(node["field"], table_alias, columns,
                            json_data_column)
    value = node["value"] if "value" in node else _context_value(
        node["valueFrom"], principal, context, {})
    if value is MISSING:
        # Runtime value absent: compile a predicate that is always false.
        return "1 = 0"
    op = node["op"]
    if op in _SQL_OPS:
        params.append(value)
        return f"({field} {_SQL_OPS[op]} ?)"
    if op in ("in", "not_in"):
        values = list(value) if isinstance(value, (list, tuple, set)) else []
        if not values:
            return "1 = 0" if op == "in" else "1 = 1"
        placeholders = ", ".join("?" for _ in values)
        params.extend(values)
        keyword = "IN" if op == "in" else "NOT IN"
        return f"({field} {keyword} ({placeholders}))"
    if op == "prefix":
        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%") \
            .replace("_", "\\_")
        params.append(escaped + "%")
        return f"({field} LIKE ? ESCAPE '\\')"
    if op == "contains":
        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%") \
            .replace("_", "\\_")
        params.append("%" + escaped + "%")
        return f"({field} LIKE ? ESCAPE '\\')"
    if op == "exists":
        return f"({field} IS NOT NULL)"
    if op == "not_exists":
        return f"({field} IS NULL)"
    return "1 = 0"


def filter_rows(rows: Iterable[Mapping[str, Any]], node: Mapping[str, Any],
                principal: Any, context: Any) -> list[dict]:
    """Apply a normalized scope to in-memory records (tests, projections)."""
    return [dict(row) for row in rows
            if evaluate_filter(node, principal, context, row)]


@dataclass(slots=True)
class Scope:
    """A named data scope from a policy document."""

    name: str
    filter: dict

    def matches(self, principal: Any, context: Any,
                resource: Mapping[str, Any]) -> bool:
        return evaluate_filter(self.filter, principal, context, resource)

    def sql(self, principal: Any, context: Any, *, table_alias: str = "",
            columns: Mapping[str, str] | None = None) -> tuple[str, list]:
        return compile_filter_sql(self.filter, principal, context,
                                  table_alias=table_alias, columns=columns)

    def to_dict(self) -> dict:
        return {"name": self.name, "filter": self.filter}
