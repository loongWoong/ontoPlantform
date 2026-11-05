"""CSV / Markdown / JSON import-export for workflow CQs.

The console should not force people to retype a CQ set that already exists in
a spreadsheet or a review document.  This module is deliberately pure: it maps
between the wire formats and the same raw dict shape ``normalize_cq`` accepts,
so imported CQs pass through exactly the same validation as hand-written ones.

The exported CSV is intentionally flat, and the importer accepts both English
field names and the common Chinese column labels.  Unknown columns are ignored,
which makes it safe to paste a spreadsheet that contains extra review notes.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..errors import ValidationError

MAX_IMPORT_CQS = 200

#: CSV columns written by export.  Keeping one canonical order makes diffs
#: between two exported rounds readable.
COLUMNS: tuple[str, ...] = (
    "code", "question", "priority", "type", "expected_kind",
    "expected_value", "expected_operator", "expected_rows",
    "expected_ordered", "expected_note", "query_kind", "object_type",
    "relation", "direction", "ref", "filters", "limit", "model_refs",
    "sample_refs", "source", "rationale", "notes",
)

_ALIASES = {
    "编号": "code", "问题": "question", "能力问题": "question",
    "优先级": "priority", "类型": "type", "cq类型": "type",
    "期望类型": "expected_kind", "期望值": "expected_value",
    "操作符": "expected_operator", "期望行": "expected_rows",
    "有序": "expected_ordered", "说明": "expected_note",
    "查询引擎": "query_kind", "引擎": "query_kind",
    "对象类型": "object_type", "关系": "relation", "方向": "direction",
    "引用": "ref", "过滤": "filters", "限制": "limit",
    "模型引用": "model_refs", "样例引用": "sample_refs",
    "来源": "source", "理由": "rationale", "备注": "notes",
    "question/能力问题": "question", "query/查询": "query_json",
}


def _text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _split_list(value: Any, limit: int = 50) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        raw = str(value).strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                items = [str(item).strip() for item in parsed]
            else:
                items = [part.strip() for part in raw.replace("，", ",").split(",")]
        else:
            items = [part.strip() for part in raw.replace("，", ",").split(",")]
    return [item for item in items if item][:limit]


def _json_object(value: Any, field: str) -> dict:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{field} must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"{field} must be a JSON object")
    return parsed


def _json_rows(value: Any, field: str) -> list[dict]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{field} must be a JSON list: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(row, dict) for row in parsed):
        raise ValidationError(f"{field} must be a JSON list of objects")
    return parsed


def _bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "否", "不")


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _raw_from_row(row: dict) -> dict:
    """Turn one flat row into the nested shape normalize_cq accepts."""
    raw: dict = {
        "code": _text(row.get("code"), 40),
        "question": _text(row.get("question"), 4000),
        "priority": _text(row.get("priority"), 10).upper(),
        "type": _text(row.get("type"), 40),
        "source": _text(row.get("source"), 500),
        "rationale": _text(row.get("rationale"), 2000),
        "notes": _text(row.get("notes"), 2000),
        "model_refs": _split_list(row.get("model_refs"), 40),
        "sample_refs": _split_list(row.get("sample_refs"), 20),
    }
    raw = {key: value for key, value in raw.items() if value not in ("", [], None)}
    if not raw.get("question") and row.get("query_json"):
        # A JSON-projected row may carry the whole object; let the caller map it.
        raise ValidationError("imported CQ is missing its question")

    expected_kind = _text(row.get("expected_kind"), 30) or "row_count"
    expected: dict = {"kind": expected_kind}
    if expected_kind == "row_count":
        expected["value"] = _int(row.get("expected_value"), 1)
        operator = _text(row.get("expected_operator"), 10) or "eq"
        expected["operator"] = operator
    elif expected_kind == "rows_contain":
        expected["rows"] = _json_rows(row.get("expected_rows"), "expected_rows")
    elif expected_kind == "golden_rows":
        expected["rows"] = _json_rows(row.get("expected_rows"), "expected_rows")
        expected["ordered"] = _bool(row.get("expected_ordered"), True)
    elif expected_kind == "boolean":
        expected["value"] = _bool(row.get("expected_value"), True)
    elif expected_kind == "manual":
        note = _text(row.get("expected_note"), 1000)
        if note:
            expected["note"] = note
    raw["expected"] = expected

    query_kind = _text(row.get("query_kind"), 20) or "fixture"
    query: dict = {"kind": query_kind}
    for key in ("object_type", "relation", "direction", "ref"):
        value = _text(row.get(key), 200)
        if value:
            query[key] = value
    filters = _json_object(row.get("filters"), "filters")
    if filters:
        query["filters"] = filters
    limit = _int(row.get("limit"), 0)
    if limit:
        query["limit"] = limit
    if query_kind == "fixture" and not query.get("object_type") and not query.get("relation"):
        # Allow a completely empty query only for manual CQs; normalize_cq will
        # report a precise error otherwise.
        query.pop("kind", None)
    raw["query"] = query
    return raw


def _row_from_cq(cq: dict) -> dict:
    expected = cq.get("expected") or {}
    query = cq.get("query") or {}
    kind = expected.get("kind") or "row_count"
    row = {column: "" for column in COLUMNS}
    row.update({
        "code": cq.get("code") or "",
        "question": cq.get("question") or "",
        "priority": cq.get("priority") or "",
        "type": cq.get("type") or "",
        "expected_kind": kind,
        "query_kind": query.get("kind") or "fixture",
        "object_type": query.get("object_type") or "",
        "relation": query.get("relation") or "",
        "direction": query.get("direction") or "",
        "ref": query.get("ref") or "",
        "filters": json.dumps(query.get("filters") or {}, ensure_ascii=False,
                              separators=(",", ":")) if query.get("filters") else "",
        "limit": query.get("limit") or "",
        "model_refs": ",".join(cq.get("model_refs") or []),
        "sample_refs": ",".join(cq.get("sample_refs") or []),
        "source": cq.get("source") or "",
        "rationale": cq.get("rationale") or "",
        "notes": cq.get("notes") or "",
    })
    if kind == "row_count":
        row["expected_value"] = expected.get("value", 1)
        row["expected_operator"] = expected.get("operator") or "eq"
    elif kind in ("rows_contain", "golden_rows"):
        row["expected_rows"] = json.dumps(
            expected.get("rows") or [], ensure_ascii=False, separators=(",", ":"))
        if kind == "golden_rows":
            row["expected_ordered"] = "true" if expected.get("ordered", True) else "false"
    elif kind == "boolean":
        row["expected_value"] = "true" if expected.get("value", True) else "false"
    elif kind == "manual":
        row["expected_note"] = expected.get("note") or ""
    return row


def export_cqs(cqs: list[dict], fmt: str = "csv") -> dict:
    """Serialize CQs to CSV, Markdown or JSON; returns a JSON-safe payload."""
    fmt = (fmt or "csv").strip().lower()
    if fmt not in ("csv", "markdown", "md", "json"):
        raise ValidationError("format must be csv|markdown|json")
    if fmt == "md":
        fmt = "markdown"
    if fmt == "json":
        content = json.dumps(
            {"cqs": [_row_from_cq(cq) for cq in cqs]},
            ensure_ascii=False, indent=2)
        return {"format": fmt, "content": content,
                "filename": "cq-set.json", "count": len(cqs)}
    if fmt == "markdown":
        header = "| " + " | ".join(COLUMNS) + " |"
        rule = "| " + " | ".join("---" for _ in COLUMNS) + " |"
        lines = [header, rule]
        for cq in cqs:
            row = _row_from_cq(cq)
            lines.append("| " + " | ".join(
                str(row.get(column, "")).replace("|", "\\|") for column in COLUMNS) + " |")
        content = "\n".join(lines) + "\n"
        return {"format": fmt, "content": content,
                "filename": "cq-set.md", "count": len(cqs)}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\n")
    writer.writeheader()
    for cq in cqs:
        writer.writerow(_row_from_cq(cq))
    return {"format": "csv", "content": "\ufeff" + buffer.getvalue(),
            "filename": "cq-set.csv", "count": len(cqs)}


def _canonical_header(name: Any) -> str:
    """Map English/Chinese spreadsheet headers onto the canonical column."""
    key = _text(name, 80).replace("\ufeff", "")
    if not key:
        return ""
    lowered = key.lower()
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    if key in _ALIASES:
        return _ALIASES[key]
    return lowered if lowered in COLUMNS else key


def _parse_csv(content: str) -> list[dict]:
    content = content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValidationError("CSV is missing a header row")
    headers = {str(name).strip(): _canonical_header(name)
               for name in reader.fieldnames if name}
    normalized: list[dict] = []
    for row in reader:
        mapped: dict = {}
        for header, value in row.items():
            key = headers.get(str(header).strip(), _canonical_header(header))
            mapped[key] = value
        if not any(str(value or "").strip() for value in mapped.values()):
            continue
        normalized.append(mapped)
    return normalized


def _parse_markdown(content: str) -> list[dict]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    table = [line for line in lines if line.startswith("|") and line.endswith("|")]
    if len(table) < 2:
        raise ValidationError("Markdown import needs a pipe table with a header")
    headers = [_canonical_header(cell)
               for cell in table[0].strip("|").split("|")]
    rows: list[dict] = []
    for line in table[1:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(set(cell) <= {"-", ":", " "} and cell for cell in cells):
            continue
        row = {headers[index]: (cells[index] if index < len(cells) else "")
               for index in range(len(headers))}
        if any(row.values()):
            rows.append(row)
    return rows


def _parse_json(content: str) -> list[dict]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc
    rows = parsed.get("cqs") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValidationError("JSON import must be a list of CQ objects or {\"cqs\": [...]}")
    if len(rows) > MAX_IMPORT_CQS:
        raise ValidationError(f"import exceeds the {MAX_IMPORT_CQS} CQ limit")
    output = []
    for row in rows:
        if isinstance(row.get("expected"), dict) or isinstance(row.get("query"), dict):
            # Already nested: preserve unknown extra keys, normalize_cq filters.
            output.append(row)
        else:
            output.append(_raw_from_row(
                {_canonical_header(key): value for key, value in row.items()}))
    return output


def parse_cqs(content: str, fmt: str = "auto") -> list[dict]:
    """Parse one import document into raw CQ dicts (not yet normalized)."""
    content = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        raise ValidationError("import content is empty")
    fmt = (fmt or "auto").strip().lower()
    if fmt == "md":
        fmt = "markdown"
    if fmt == "auto":
        stripped = content.lstrip("\ufeff").strip()
        if stripped.startswith("{") or stripped.startswith("["):
            fmt = "json"
        elif stripped.startswith("|"):
            fmt = "markdown"
        else:
            fmt = "csv"
    if fmt == "csv":
        rows = _parse_csv(content)
    elif fmt == "markdown":
        rows = _parse_markdown(content)
    elif fmt == "json":
        return _parse_json(content)
    elif fmt in ("xlsx", "xls"):
        raise ValidationError("Excel import is not enabled; export CSV and import that")
    else:
        raise ValidationError("format must be auto|csv|markdown|json")
    if len(rows) > MAX_IMPORT_CQS:
        raise ValidationError(f"import exceeds the {MAX_IMPORT_CQS} CQ limit")
    output: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("expected"), dict) or isinstance(row.get("query"), dict):
            output.append(row)
        else:
            output.append(_raw_from_row(row))
    return output
