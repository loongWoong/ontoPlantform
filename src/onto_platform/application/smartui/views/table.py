"""DataTable view plugin.

The plugin owns the *presentation* contract for a table: which render options
are legal, how filters/sort/limit are interpreted, and how the runtime payload
is resolved from the same records the deterministic bundle already uses.

It does not own ontology semantics or compilation: the perspective whitelist
and the engine's component mapping stay core.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chartType": {"enum": ["table"]},
        "display": {"enum": ["panel", "table"]},
        "groupBy": {"type": "string", "x-ontology": "property"},
        "metric": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "x-ontology": "property"},
                "agg": {"enum": ["sum", "avg", "count", "min", "max"]},
            },
        },
        "sort": {
            "type": "object",
            "properties": {
                "field": {"type": "string"},
                "direction": {"enum": ["asc", "desc"]},
            },
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "columns": {"type": "array", "items": {"type": "string"},
                    "x-ontology": "property"},
        "filters": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "x-ontology": "property"},
                    "op": {"enum": ["eq", "ne", "gt", "gte", "lt", "lte",
                                    "contains", "in"]},
                    "value": {},
                },
            },
        },
    },
}


def table_request(blueprint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(blueprint, dict):
        return None
    for view in blueprint.get("views") or []:
        if not isinstance(view, dict):
            continue
        options = view.get("options") or {}
        if options.get("chartType") == "table" or options.get("display") == "table":
            return options
    return None


def relative_cutoff(target: str) -> datetime | None:
    match = re.fullmatch(r"^-(\d+)\s*([dhwm])$", str(target or "").strip(),
                         re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = {"h": 3600, "d": 86400, "w": 7 * 86400, "m": 30 * 86400}[unit]
    return datetime.now(timezone.utc) - timedelta(seconds=amount * seconds)


def as_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def table_match(value: Any, op: str, target: Any) -> bool:
    text = "" if value is None else str(value)
    if op in {"gt", "gte", "lt", "lte"}:
        cutoff = relative_cutoff(str(target)) if isinstance(target, str) else None
        current = as_datetime(value)
        if cutoff is not None and current is not None:
            left, right = current, cutoff
        else:
            try:
                left = float(text)
                right = float(target)
            except (TypeError, ValueError):
                left, right = text, str(target)
        return {
            "gt": left > right, "gte": left >= right,
            "lt": left < right, "lte": left <= right,
        }[op]
    if isinstance(target, bool) or isinstance(value, bool):
        flag = text.strip().lower() in {"true", "1", "yes", "是"}
        return (flag == target) if op == "eq" else (flag != target)
    if op in {"eq", "ne"}:
        try:
            left: Any = float(text)
            right = float(target)
        except (TypeError, ValueError):
            left, right = text.strip().lower(), str(target).strip().lower()
        return (left == right) if op == "eq" else (left != right)
    if op == "contains":
        return str(target).lower() in text.lower()
    if op == "in":
        candidates = target if isinstance(target, (list, tuple, set)) else [target]
        return text.lower() in {str(item).strip().lower() for item in candidates}
    return False


def adjudicate_options(raw: Any, obj: Any, perspective: str,
                       pruned: list[dict[str, Any]]) -> dict[str, Any]:
    """Adjudicate v1.1 render options; only whitelisted, ontology-backed fields."""
    if not isinstance(raw, dict):
        return {}
    props = {prop.name for prop in obj.properties}
    options: dict[str, Any] = {}

    def drop(key: str, reason: str) -> None:
        pruned.append({"kind": "option", "key": key, "reason": reason})

    chart_type = raw.get("chartType")
    if chart_type in {"bar", "line", "pie", "table"}:
        if chart_type == "table" and perspective != "statistics":
            drop("chartType", "table 仅支持 statistics 视角")
        else:
            options["chartType"] = chart_type
    elif chart_type:
        drop("chartType", "不在白名单 bar|line|pie|table")
    display = raw.get("display")
    if display in {"panel", "table"}:
        if display == "table" and perspective != "statistics":
            drop("display", "table 仅支持 statistics 视角")
        else:
            options["display"] = display
    group_by = str(raw.get("groupBy") or "")
    if group_by:
        if group_by in props:
            options["groupBy"] = group_by
        else:
            drop("groupBy", f"{group_by!r} 不是对象属性")
    metric = raw.get("metric")
    if isinstance(metric, dict) and metric.get("field"):
        field = str(metric["field"])
        agg = str(metric.get("agg") or "")
        if field in props and agg in {"sum", "avg", "count", "min", "max"}:
            options["metric"] = {"field": field, "agg": agg}
        else:
            drop("metric", f"{field!r}/{agg!r} 不是合法属性或聚合")
    sort = raw.get("sort")
    if isinstance(sort, dict) and sort.get("field"):
        field = str(sort["field"])
        if field in props or (group_by and field in {"value", "count"}):
            options["sort"] = {
                "field": field,
                "direction": "asc" if sort.get("direction") == "asc" else "desc",
            }
        else:
            drop("sort", f"{field!r} 不是对象属性")
    try:
        limit = int(raw.get("limit"))
    except (TypeError, ValueError):
        limit = None
    if limit is not None:
        if 1 <= limit <= 200:
            options["limit"] = limit
        else:
            drop("limit", "limit 必须在 1-200 之间")
    columns = raw.get("columns")
    if isinstance(columns, list):
        kept = [str(item) for item in columns if str(item) in props][:24]
        if kept:
            options["columns"] = kept
        elif columns:
            drop("columns", "列名都不属于该对象")
    filters = raw.get("filters")
    if isinstance(filters, list):
        kept_filters: list[dict[str, Any]] = []
        for item in filters[:12]:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            op = str(item.get("op") or "")
            if field not in props or op not in {
                    "eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"}:
                drop("filters", f"过滤条件 {field!r}/{op!r} 不合法")
                continue
            kept_filters.append({
                "field": field, "op": op,
                "value": item.get("value"),
            })
        if kept_filters:
            options["filters"] = kept_filters
    return options


def table_payload(context: dict[str, Any],
                  options: dict[str, Any]) -> dict[str, Any]:
    """Rows for a Blueprint v1.1 DataTable, derived from real workspace records.

    Filters/sort/limit are applied to the same record set the bundle already
    uses; unknown fields were pruned by the Studio adjudicator and the engine.
    """
    # Imported lazily: the bridge imports this plugin to resolve view payloads,
    # so a top-level import would create a cycle.
    from ..bridge import _as_float, _prop_label, _record_value, _value_text

    records = list(context.get("records") or [])
    spec = context.get("focus") or {}
    raw_props = spec.get("properties") if isinstance(spec, dict) else None
    if isinstance(raw_props, dict):
        props = [str(name) for name in raw_props]
    else:
        props = [
            str(prop.get("name") if isinstance(prop, dict)
                else getattr(prop, "name", ""))
            for prop in (raw_props or [])
        ]
    props = [name for name in props if name]
    filters = options.get("filters") or []
    if filters:
        records = [
            record for record in records
            if all(table_match(_record_value(record, str(item.get("field"))),
                               str(item.get("op")), item.get("value"))
                   for item in filters)
        ]
    sort = options.get("sort") or {}
    group_by = str(options.get("groupBy") or "")
    try:
        limit = int(options.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(200, limit))

    if group_by:
        metric = options.get("metric") or {}
        metric_field = str(metric.get("field") or "")
        agg = str(metric.get("agg") or "count")
        buckets: dict[str, list[dict]] = {}
        for record in records:
            key = _value_text(_record_value(record, group_by)) or "未分组"
            buckets.setdefault(key, []).append(record)

        def aggregate(bucket: list[dict]) -> float:
            if agg == "count" or not metric_field:
                return float(len(bucket))
            values = [
                _as_float(_record_value(record, metric_field), default=float("nan"))
                for record in bucket
            ]
            values = [value for value in values if value == value]
            if not values:
                return 0.0
            if agg == "sum":
                return sum(values)
            if agg == "avg":
                return sum(values) / len(values)
            if agg == "min":
                return min(values)
            return max(values)

        grouped = [
            (key, aggregate(bucket)) for key, bucket in buckets.items()
        ]
        field = str(sort.get("field") or "")
        if field == group_by:
            grouped.sort(key=lambda item: item[0],
                         reverse=sort.get("direction") != "asc")
        elif field:
            grouped.sort(key=lambda item: item[1],
                         reverse=sort.get("direction") != "asc")
        total = len(grouped)
        grouped = grouped[:limit]
        columns = [group_by, "__value"]
        labels = {group_by: _prop_label(group_by)}
        if agg == "count" or not metric_field:
            labels["__value"] = "记录数"
            rows = [
                {group_by: key, "__value": f"{value:.0f}"}
                for key, value in grouped
            ]
        else:
            agg_label = {"sum": "合计", "avg": "均值", "min": "最小",
                         "max": "最大"}.get(agg, agg)
            labels["__value"] = f"{_prop_label(metric_field)}{agg_label}"
            rows = [
                {group_by: key, "__value": f"{value:.2f}"}
                for key, value in grouped
            ]
        return {
            "columns": columns,
            "columnLabels": labels,
            "rows": rows,
            "total": total,
        }

    if isinstance(sort, dict) and sort.get("field"):
        field = str(sort["field"])

        def sort_key(record: dict) -> tuple[bool, Any]:
            value = _record_value(record, field)
            if value is None:
                return True, ""
            try:
                return False, float(value)
            except (TypeError, ValueError):
                return False, str(value)

        records.sort(key=sort_key, reverse=sort.get("direction") != "asc")
    requested_columns = [str(item) for item in options.get("columns") or []
                         if str(item) in props]
    columns = requested_columns or props[:12]
    total = len(records)
    records = records[:limit]
    rows = [
        {column: _value_text(_record_value(record, column)) or ""
         for column in columns}
        for record in records
    ]
    return {
        "columns": columns,
        "columnLabels": {column: _prop_label(column) for column in columns},
        "rows": rows,
        "total": total,
    }


class TablePlugin:
    """Built-in DataTable presentation contribution."""

    id = "onto.smartui.view.table"
    version = "1.0.0"
    perspective = "statistics"
    view_type = "table"
    component = "DataTable"

    def options_schema(self) -> dict[str, Any]:
        return OPTIONS_SCHEMA

    def adjudicate_options(self, raw: Any, obj: Any, perspective: str,
                           pruned: list[dict[str, Any]]) -> dict[str, Any]:
        return adjudicate_options(raw, obj, perspective, pruned)

    def resolve(self, context: dict[str, Any],
                options: dict[str, Any]) -> dict[str, Any]:
        return table_payload(context, options)
