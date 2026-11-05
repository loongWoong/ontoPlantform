"""Pure domain rules for the lightweight ontology workflow.

Everything here is intentionally runtime-free: the application layer loads
platform documents and calls these helpers, while the HTTP/UI layers only
render their output.  Keeping the rules pure means the *meaning* of "step
done", "CQ passed" and "evidence is fresh" can be unit-tested without a
kernel or a database.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ...errors import ValidationError

#: A round is a batch of CQs, not a waterfall phase.  Only publish is a hard
#: lifecycle transition; the eight steps are computed progress, so a team can
#: go back to an earlier step without fighting a state machine.
ROUND_STATUSES: tuple[str, ...] = ("active", "in_review", "accepted")

#: CQ priority is used by the acceptance gate (P0 must be 100%).
CQ_PRIORITIES: tuple[str, ...] = ("P0", "P1", "P2")

#: scoping -> why the ontology exists; competency -> what it must answer;
#: validation -> a data-shape check that is useful while modelling.
CQ_TYPES: tuple[str, ...] = ("scoping", "competency", "validation")

#: CQ status is derived from the latest acceptance run, except ``manual``.
CQ_STATUSES: tuple[str, ...] = (
    "open", "ready", "passed", "failed", "error", "manual")

#: What a CQ expects from the sample query.
EXPECTED_KINDS: tuple[str, ...] = (
    "rows_contain", "row_count", "golden_rows", "empty", "boolean", "manual")

#: Query evaluator families.  ``fixture`` is the default because samples are
#: isolated by construction; ``runtime`` is reserved for the future SPARQL /
#: platform-state adapter and currently reports an explicit error.
QUERY_KINDS: tuple[str, ...] = ("fixture", "runtime")

#: Default first-round targets (design: 5-10 CQs, P0 100%, overall >= 80%).
DEFAULT_TARGET: dict = {
    "min_cq": 5,
    "max_cq": 10,
    "p0_pass_rate": 100,
    "overall_pass_rate": 80,
}

STEP_TITLES: tuple[tuple[str, str], ...] = (
    ("scope", "范围"),
    ("cq", "编写能力问题"),
    ("reuse", "复用调研"),
    ("sketch", "概念草图"),
    ("formalize", "形式化"),
    ("samples", "样例实例"),
    ("acceptance", "CQ 验证"),
    ("review", "评审发布"),
)

_SKETCH_ACTIONS = {
    "scope": "在「范围」里写清本轮要回答什么、不回答什么。",
    "cq": "至少写出目标数量的能力问题，并标出 P0。",
    "reuse": "记录一次复用调研（或显式标记本轮不复用）。",
    "sketch": "在「结构」标签页添加对象类型与关系。",
    "formalize": "运行「发布前检查」，修掉语义问题。",
    "samples": "添加一组样例实例，覆盖 P0 问题。",
    "acceptance": "运行 CQ 验证；失败时按结果回到结构或样例。",
    "review": "提交评审并记录结论，然后发布本轮版本。",
}


def _text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _string_list(value: Any, limit: int = 50) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.replace("，", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        raise ValidationError("expected a list of strings")
    result: list[str] = []
    for item in value:
        text = _text(item, 500)
        if text and text not in result:
            result.append(text)
    return result[:limit]


def _number(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"expected a number, got {value!r}") from None


def _int(value: Any, default: int, *, minimum: int = 0,
         maximum: int = 100000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def canonical_hash(value: Any) -> str:
    """Stable 16-char hash of a JSON-serialisable value."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def ontology_fingerprint(ontology: dict | None) -> str:
    """Fingerprint the *model content*, not lifecycle/version counters.

    Validation changes ``lifecycle`` and publish changes ``version``/
    ``schema_version``; neither changes the meaning of a CQ, so they must not
    invalidate acceptance evidence.  Objects, relations, vocabulary and the
    logical profile are the evidence-bearing part.
    """
    ontology = ontology or {}
    content = {
        "profile": ontology.get("profile", "closed"),
        "objects": ontology.get("objects") or {},
        "relations": ontology.get("relations") or {},
        "vocabulary": ontology.get("vocabulary") or {},
    }
    return canonical_hash(content)


def sample_fingerprint(sample: dict | None) -> str:
    sample = sample or {}
    return canonical_hash({
        "objects": sample.get("objects") or {},
        "relations": sample.get("relations") or [],
    })


def sample_problems(sample: dict, ontology: dict | None) -> list[str]:
    """Check a sample set against the current ontology shape.

    These are authoring hints, not a hard rejection: a modeller may write a
    sample just before adding the property.  The console shows them next to
    the sample, and publish-check keeps them as warnings so typos cannot hide.
    """
    ontology = ontology or {}
    object_specs = ontology.get("objects") or {}
    relation_specs = ontology.get("relations") or {}
    problems: list[str] = []
    known_refs: set[str] = set()
    objects = sample.get("objects") or {}
    for type_name, rows in objects.items():
        spec = object_specs.get(type_name)
        if not isinstance(spec, dict):
            problems.append(f"样例引用了本体里不存在的对象类型：{type_name}")
            continue
        props = spec.get("properties") or {}
        allowed = set(props) if isinstance(props, dict) else {
            str(item.get("name")) for item in props if isinstance(item, dict)}
        for row in rows:
            row_id = str(row.get("id") or "")
            known_refs.add(f"{type_name}/{row_id}")
            values = row.get("properties") or {}
            for prop in values:
                if prop not in allowed:
                    problems.append(
                        f"{type_name}/{row_id} 使用了未定义属性：{prop}")
    for edge in sample.get("relations") or []:
        name = str(edge.get("name") or "")
        spec = relation_specs.get(name)
        if not isinstance(spec, dict):
            problems.append(f"样例引用了本体里不存在的关系：{name}")
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in known_refs:
            problems.append(f"关系 {name} 的起点不在样例对象里：{source}")
        if target not in known_refs:
            problems.append(f"关系 {name} 的终点不在样例对象里：{target}")
        source_type = source.split("/", 1)[0]
        target_type = target.split("/", 1)[0]
        if spec.get("source") and source_type != spec.get("source"):
            problems.append(
                f"关系 {name} 的起点应为 {spec.get('source')}，实际 {source_type}")
        if spec.get("target") and target_type != spec.get("target"):
            problems.append(
                f"关系 {name} 的终点应为 {spec.get('target')}，实际 {target_type}")
    return problems[:50]


def _scope(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "summary": _text(raw.get("summary"), 4000),
        "in_scope": _string_list(raw.get("in_scope"), 60),
        "out_of_scope": _string_list(raw.get("out_of_scope"), 60),
        "actors": _string_list(raw.get("actors"), 40),
        "decisions": _string_list(raw.get("decisions"), 60),
    }


def _target(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    min_cq = _int(raw.get("min_cq"), DEFAULT_TARGET["min_cq"], minimum=1,
                  maximum=50)
    max_cq = _int(raw.get("max_cq"), DEFAULT_TARGET["max_cq"], minimum=1,
                  maximum=100)
    if max_cq < min_cq:
        max_cq = min_cq
    p0_rate = max(0.0, min(100.0, _number(
        raw.get("p0_pass_rate"), DEFAULT_TARGET["p0_pass_rate"])))
    overall_rate = max(0.0, min(100.0, _number(
        raw.get("overall_pass_rate"), DEFAULT_TARGET["overall_pass_rate"])))
    return {
        "min_cq": min_cq,
        "max_cq": max_cq,
        "p0_pass_rate": p0_rate,
        "overall_pass_rate": overall_rate,
    }


def normalize_round(raw: dict, *, workspace_id: str, round_id: str, seq: int,
                    now: str, actor: str) -> dict:
    """Build the persisted round document from a create/patch payload."""
    raw = raw if isinstance(raw, dict) else {}
    name = _text(raw.get("name"), 120) or f"第 {seq} 轮"
    goal = _text(raw.get("goal"), 2000)
    status = _text(raw.get("status"), 20) or "active"
    if status not in ROUND_STATUSES:
        raise ValidationError(f"unknown round status: {status!r}")
    return {
        "id": round_id,
        "workspace_id": workspace_id,
        "kind": "round",
        "seq": seq,
        "name": name,
        "goal": goal,
        "status": status,
        "scope": _scope(raw.get("scope")),
        "target": _target(raw.get("target")),
        "reuse_skipped": bool(raw.get("reuse_skipped", False)),
        "validation": raw.get("validation") or {},
        "ontology_id": _text(raw.get("ontology_id"), 80),
        "ontology_version": _int(raw.get("ontology_version"), 0),
        "ontology_fingerprint": _text(
            raw.get("ontology_fingerprint"), 40),
        "published_version": raw.get("published_version"),
        "published_at": _text(raw.get("published_at"), 40) or None,
        "created_by": _text(raw.get("created_by"), 120) or actor,
        "created_at": _text(raw.get("created_at"), 40) or now,
        "updated_at": now,
    }


def normalize_cq(raw: dict, *, workspace_id: str, round_id: str, cq_id: str,
                 now: str, default_code: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    question = _text(raw.get("question"), 4000)
    if not question:
        raise ValidationError("CQ question is required")
    code = _text(raw.get("code"), 40) or default_code
    cq_type = _text(raw.get("type"), 40) or "competency"
    if cq_type not in CQ_TYPES:
        raise ValidationError(f"unknown CQ type: {cq_type!r}")
    priority = _text(raw.get("priority"), 10).upper() or "P1"
    if priority not in CQ_PRIORITIES:
        raise ValidationError(f"unknown CQ priority: {priority!r}")
    expected = _expected(raw.get("expected"))
    query = _query(raw.get("query"), manual=expected["kind"] == "manual")
    return {
        "id": cq_id,
        "workspace_id": workspace_id,
        "round_id": round_id,
        "kind": "cq",
        "code": code,
        "question": question,
        "type": cq_type,
        "priority": priority,
        "source": _text(raw.get("source"), 500),
        "rationale": _text(raw.get("rationale"), 2000),
        "expected": expected,
        "query": query,
        "model_refs": _string_list(raw.get("model_refs"), 40),
        "sample_refs": _string_list(raw.get("sample_refs"), 20),
        "notes": _text(raw.get("notes"), 2000),
        "created_at": _text(raw.get("created_at"), 40) or now,
        "updated_at": now,
    }


def _expected(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    kind = _text(raw.get("kind"), 30) or "row_count"
    if kind not in EXPECTED_KINDS:
        raise ValidationError(f"unknown CQ expectation: {kind!r}")
    out: dict = {"kind": kind}
    if kind == "rows_contain":
        rows = raw.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValidationError(
                "rows_contain expects a non-empty 'rows' list")
        if not all(isinstance(row, dict) for row in rows):
            raise ValidationError("rows_contain rows must be objects")
        out["rows"] = rows[:100]
    elif kind == "golden_rows":
        rows = raw.get("rows")
        if not isinstance(rows, list):
            raise ValidationError("golden_rows expects a 'rows' list")
        out["rows"] = rows[:100]
        out["ordered"] = bool(raw.get("ordered", True))
    elif kind == "row_count":
        out["value"] = _int(raw.get("value"), 1, minimum=0)
        operator = _text(raw.get("operator"), 10) or "eq"
        if operator not in ("eq", "gte", "lte"):
            raise ValidationError(
                f"row_count operator must be eq|gte|lte, got {operator!r}")
        out["operator"] = operator
    elif kind == "boolean":
        out["value"] = bool(raw.get("value", True))
    elif kind == "manual":
        out["note"] = _text(raw.get("note"), 1000)
    return out


def _query(raw: Any, *, manual: bool) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    if manual:
        return {}
    kind = _text(raw.get("kind"), 20) or "fixture"
    if kind not in QUERY_KINDS:
        raise ValidationError(f"unknown CQ query kind: {kind!r}")
    object_type = _text(raw.get("object_type"), 120)
    relation = _text(raw.get("relation"), 120)
    ref = _text(raw.get("ref"), 200)
    if kind == "fixture" and not object_type and not relation:
        raise ValidationError(
            "fixture query needs either 'object_type' or 'relation'")
    filters = raw.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise ValidationError("query filters must be an object")
    query = {
        "kind": kind,
        "object_type": object_type,
        "relation": relation,
        "direction": _text(raw.get("direction"), 10) or "out",
        "ref": ref,
        "filters": dict(filters or {}),
        "limit": _int(raw.get("limit"), 200, minimum=0, maximum=1000),
    }
    if query["direction"] not in ("out", "in", "both"):
        raise ValidationError("query direction must be out|in|both")
    return query


def normalize_reuse(raw: dict, *, workspace_id: str, round_id: str,
                    reuse_id: str, now: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    decision = _text(raw.get("decision"), 20) or "build"
    if decision not in ("reuse", "extend", "build", "skip"):
        raise ValidationError(f"unknown reuse decision: {decision!r}")
    title = _text(raw.get("title"), 200)
    if not title:
        raise ValidationError("reuse record needs a title")
    return {
        "id": reuse_id,
        "workspace_id": workspace_id,
        "round_id": round_id,
        "kind": "reuse",
        "title": title,
        "decision": decision,
        "source": _text(raw.get("source"), 500),
        "reference": _text(raw.get("reference"), 1000),
        "note": _text(raw.get("note"), 2000),
        "owner": _text(raw.get("owner"), 120),
        "created_at": _text(raw.get("created_at"), 40) or now,
        "updated_at": now,
    }


def normalize_sample(raw: dict, *, workspace_id: str, round_id: str,
                     sample_id: str, now: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    name = _text(raw.get("name"), 120) or "样例集"
    objects_raw = raw.get("objects") or {}
    if not isinstance(objects_raw, dict):
        raise ValidationError("sample objects must map object types to rows")
    objects: dict[str, list[dict]] = {}
    total = 0
    for type_name, rows in objects_raw.items():
        type_name = _text(type_name, 120)
        if not type_name:
            raise ValidationError("sample object type must not be empty")
        if not isinstance(rows, list):
            raise ValidationError(
                f"sample rows for {type_name} must be a list")
        if len(rows) > 500:
            raise ValidationError(
                f"sample rows for {type_name} exceed the 500 row limit")
        normalized: list[dict] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValidationError(
                    f"sample row {type_name}[{index}] must be an object")
            row_id = _text(row.get("id") or row.get("_id"), 200)
            if not row_id:
                row_id = _text(row.get("identity"), 200) or f"{type_name}-{index + 1}"
            if row_id in seen:
                raise ValidationError(
                    f"duplicate sample id {row_id!r} in {type_name}")
            seen.add(row_id)
            properties = row.get("properties")
            if isinstance(properties, dict):
                values = dict(properties)
            else:
                values = {k: v for k, v in row.items()
                          if k not in ("id", "_id", "identity")}
            normalized.append({"id": row_id, "properties": values})
        objects[type_name] = normalized
        total += len(normalized)
        if total > 2000:
            raise ValidationError("sample set exceeds the 2000 row limit")
    relations_raw = raw.get("relations") or []
    if not isinstance(relations_raw, list):
        raise ValidationError("sample relations must be a list")
    relations = []
    for index, relation in enumerate(relations_raw):
        if not isinstance(relation, dict):
            raise ValidationError(
                f"sample relation {index} must be an object")
        name = _text(relation.get("name"), 120)
        source = _text(relation.get("source"), 300)
        target = _text(relation.get("target"), 300)
        if not name or not source or not target:
            raise ValidationError(
                f"sample relation {index} needs name, source and target")
        relations.append({"name": name, "source": source, "target": target})
    return {
        "id": sample_id,
        "workspace_id": workspace_id,
        "round_id": round_id,
        "kind": "sample",
        "name": name,
        "description": _text(raw.get("description"), 2000),
        "source": _text(raw.get("source"), 500),
        "objects": objects,
        "relations": relations,
        "row_count": total,
        "created_at": _text(raw.get("created_at"), 40) or now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# sample query evaluator (isolated by construction, no kernel write path)
# ---------------------------------------------------------------------------

_RESERVED_ROW_KEYS = {"id", "properties"}


def _row_values(row: dict) -> dict:
    values = dict(row.get("properties") or {})
    for key, value in row.items():
        if key != "properties" and key not in values:
            values[key] = value
    values["id"] = row.get("id")
    return values


def _matches(value: Any, expected: Any) -> bool:
    if isinstance(value, bool) or isinstance(expected, bool):
        return value == expected
    if isinstance(value, (int, float)) and isinstance(expected, (int, float)):
        return value == expected
    return str(value) == str(expected)


def _match_filter(value: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return _matches(value, expected)
    if op == "ne":
        return not _matches(value, expected)
    if op == "contains":
        if isinstance(value, list):
            return expected in value
        if isinstance(value, str):
            return str(expected) in value
        return False
    if op == "in":
        choices = expected if isinstance(expected, list) else [
            part.strip() for part in str(expected).split(",") if part.strip()]
        return any(_matches(value, choice) for choice in choices)
    if op == "exists":
        wanted = bool(expected)
        return (value is not None) == wanted
    if op in ("gt", "gte", "lt", "lte"):
        try:
            left, right = float(value), float(expected)
        except (TypeError, ValueError):
            return False
        return {"gt": left > right, "gte": left >= right,
                "lt": left < right, "lte": left <= right}[op]
    raise ValidationError(f"unsupported filter operator: {op!r}")


def evaluate_query(sample: dict, query: dict) -> list[dict]:
    """Evaluate one CQ query against a single sample set (pure, isolated)."""
    query = query or {}
    if query.get("kind") == "runtime":
        raise ValidationError(
            "runtime queries are evaluated by the application use case, "
            "not by the pure fixture evaluator")
    object_type = query.get("object_type") or ""
    relation = query.get("relation") or ""
    if relation:
        direction = query.get("direction") or "out"
        ref = query.get("ref") or ""
        rows: list[dict] = []
        for edge in sample.get("relations") or []:
            if edge.get("name") != relation:
                continue
            if direction in ("out", "both") and edge.get("source") == ref:
                rows.append({"id": edge.get("target"), "ref": edge.get("target")})
            if direction in ("in", "both") and edge.get("target") == ref:
                rows.append({"id": edge.get("source"), "ref": edge.get("source")})
        return rows
    if not object_type:
        raise ValidationError("query.object_type is required")
    rows = list((sample.get("objects") or {}).get(object_type) or [])
    filters = query.get("filters") or {}
    for key, expected in filters.items():
        prop, sep, op = str(key).partition("__")
        op = op or "eq"
        rows = [row for row in rows
                if _match_filter(_row_values(row).get(prop), op, expected)]
    limit = int(query.get("limit") or 0)
    return rows[:limit] if limit else rows


def _contains(expected_row: dict, actual_row: dict) -> bool:
    values = _row_values(actual_row)
    for key, expected in expected_row.items():
        if key == "properties" and isinstance(expected, dict):
            if not _contains(expected, actual_row):
                return False
            continue
        if not _matches(values.get(key), expected):
            return False
    return True


def evaluate_expectation(expected: dict, rows: list[dict]) -> tuple[
        str, dict, str]:
    """Return ``(status, actual, detail)`` for one CQ result."""
    expected = expected or {}
    kind = expected.get("kind") or "row_count"
    if kind == "manual":
        return "manual", {"kind": "manual", "rows": rows}, (
            expected.get("note") or "人工确认，不进入自动通过率")
    actual = {"kind": kind, "row_count": len(rows), "rows": rows[:50]}
    if kind == "rows_contain":
        wanted = expected.get("rows") or []
        missing = [wanted_row for wanted_row in wanted
                   if not any(_contains(wanted_row, row) for row in rows)]
        if missing:
            return "failed", actual, (
                f"缺少预期行 {len(missing)} 条：{json.dumps(missing[:3], ensure_ascii=False)}")
        return "passed", actual, "预期行全部命中"
    if kind == "golden_rows":
        wanted = expected.get("rows") or []
        ordered = expected.get("ordered", True)
        if ordered:
            ok = ([_row_values(row) for row in rows]
                  == [dict(row) for row in wanted])
        else:
            ok = sorted((canonical_hash(_row_values(row)) for row in rows)) == \
                sorted(canonical_hash(row) for row in wanted)
        return ("passed" if ok else "failed", actual,
                "完全匹配黄金行" if ok else "与黄金行不一致")
    if kind == "row_count":
        value = int(expected.get("value", 1))
        operator = expected.get("operator") or "eq"
        ok = {"eq": len(rows) == value, "gte": len(rows) >= value,
              "lte": len(rows) <= value}[operator]
        return ("passed" if ok else "failed", actual,
                f"实际 {len(rows)} 行，期望 {operator} {value}")
    if kind == "empty":
        ok = not rows
        return ("passed" if ok else "failed", actual,
                "结果为空" if ok else f"期望为空，实际 {len(rows)} 行")
    if kind == "boolean":
        ok = bool(rows) == bool(expected.get("value", True))
        return ("passed" if ok else "failed", actual,
                "布尔结果符合预期" if ok else "布尔结果与预期相反")
    return "error", actual, f"unsupported expectation: {kind!r}"


def acceptance_summary(results: list[dict], target: dict | None = None) -> dict:
    """Aggregate CQ results into the pass rates used by the publish gate."""
    target = {**DEFAULT_TARGET, **(target or {})}
    counts = {"passed": 0, "failed": 0, "error": 0, "manual": 0}
    p0 = {"passed": 0, "total": 0}
    for result in results:
        status = result.get("status") or "error"
        if status not in counts:
            status = "error"
        counts[status] += 1
        if result.get("priority") == "P0" and status != "manual":
            p0["total"] += 1
            if status == "passed":
                p0["passed"] += 1
    automated = counts["passed"] + counts["failed"] + counts["error"]
    overall = ((counts["passed"] / automated) * 100) if automated else 0.0
    p0_rate = ((p0["passed"] / p0["total"]) * 100) if p0["total"] else 100.0
    return {
        **counts,
        "automated": automated,
        "pass_rate": round(overall, 1),
        "p0_total": p0["total"],
        "p0_passed": p0["passed"],
        "p0_pass_rate": round(p0_rate, 1),
        "meets_target": (overall >= float(target["overall_pass_rate"])
                         and p0_rate >= float(target["p0_pass_rate"])),
        "target": target,
    }


# ---------------------------------------------------------------------------
# computed steps
# ---------------------------------------------------------------------------

def _latest(items: list[dict], key: str = "created_at") -> dict | None:
    if not items:
        return None
    return max(items, key=lambda item: (int(item.get("seq") or 0),
                                        str(item.get(key) or ""),
                                        str(item.get("id") or "")))


def _sample_total(samples: list[dict]) -> int:
    return sum(int(sample.get("row_count") or 0) for sample in samples)


def _run_is_fresh(run: dict | None, fingerprint: str) -> bool:
    return bool(run and fingerprint
                and run.get("ontology_fingerprint") == fingerprint)


def compute_steps(*, round_doc: dict, ontology: dict | None,
                  cqs: list[dict], reuse: list[dict], samples: list[dict],
                  runs: list[dict], reviews: list[dict],
                  validation: dict | None, fingerprint: str) -> list[dict]:
    """Compute the eight-step progress of one round.

    The result is the UI contract: ``key``/``no``/``title``/``status``/
    ``detail``/``action``.  A step is never "blocked" by an earlier step;
    people are allowed to work in the order that makes sense for their batch.
    """
    target = {**DEFAULT_TARGET, **(round_doc.get("target") or {})}
    scope = round_doc.get("scope") or {}
    latest_run = _latest(runs)
    latest_review = _latest(reviews)
    approved = latest_review is not None and \
        latest_review.get("decision") == "approve"
    validation = validation or {}
    ontology = ontology or {}
    objects = ontology.get("objects") or {}
    relations = ontology.get("relations") or {}
    cq_count = len(cqs)
    sample_total = _sample_total(samples)

    done: list[bool] = []
    warn = False
    details: list[str] = []

    scope_done = bool(scope.get("summary") or scope.get("in_scope")
                      or round_doc.get("goal"))
    done.append(scope_done)
    details.append("范围已记录" if scope_done else "还没有写范围")

    cq_done = cq_count >= int(target["min_cq"])
    done.append(cq_done)
    if cq_done:
        details.append(f"{cq_count} 个 CQ（目标 ≥ {target['min_cq']}）")
    elif cq_count:
        details.append(f"{cq_count}/{target['min_cq']} 个 CQ")
    else:
        details.append("还没有能力问题")

    reuse_done = bool(reuse) or bool(round_doc.get("reuse_skipped"))
    done.append(reuse_done)
    details.append("已记录复用调研" if reuse_done
                   else "还没有复用调研（可显式标记不复用）")

    sketch_done = bool(objects)
    done.append(sketch_done)
    details.append(f"{len(objects)} 个对象 / {len(relations)} 条关系"
                   if sketch_done else "还没有对象类型")

    formal_ok = bool(validation.get("ok")) and \
        validation.get("ontology_fingerprint") == fingerprint
    if validation and validation.get("ontology_fingerprint") != fingerprint:
        details.append("语义检查结果已过期（本体改过）")
        warn = True
    else:
        details.append("语义检查通过" if formal_ok else "还没有通过语义检查")
    done.append(formal_ok)

    samples_done = (bool(samples) and sample_total > 0) or (
        bool(cqs) and not any(
            (cq.get("query") or {}).get("kind") != "runtime"
            and (cq.get("expected") or {}).get("kind") != "manual"
            for cq in cqs))
    done.append(samples_done)
    if bool(samples) and sample_total > 0:
        details.append(f"{len(samples)} 组样例 / {sample_total} 行实例")
    elif samples_done:
        details.append("CQ 使用运行时查询，不需要样例集")
    else:
        details.append("还没有样例实例")

    acceptance_done = bool(
        latest_run and latest_run.get("status") == "passed"
        and _run_is_fresh(latest_run, fingerprint)
        and (latest_run.get("summary") or {}).get("meets_target"))
    if latest_run and not _run_is_fresh(latest_run, fingerprint):
        details.append("最近一次验证证据已过期（本体或样例改过）")
        warn = True
    elif latest_run and latest_run.get("status") != "passed":
        details.append("最近一次验证未通过")
        warn = True
    elif latest_run:
        summary = latest_run.get("summary") or {}
        details.append(
            f"通过率 {summary.get('pass_rate', 0)}% / P0 "
            f"{summary.get('p0_pass_rate', 0)}%")
    else:
        details.append("还没有运行 CQ 验证")
    done.append(acceptance_done)

    accepted = round_doc.get("status") == "accepted"
    if accepted:
        details.append(f"已发布 v{round_doc.get('published_version') or '?'}")
        review_done = True
    elif approved:
        details.append(f"已评审通过（{latest_review.get('actor') or '?'}），待发布")
        review_done = False
        if not _run_is_fresh(latest_run, fingerprint):
            warn = True
    else:
        details.append("还没有评审通过记录")
        review_done = False
    done.append(review_done)

    first_open = next((index for index, value in enumerate(done)
                       if not value), None)
    steps = []
    for index, (key, title) in enumerate(STEP_TITLES):
        if done[index]:
            status = "done"
        elif index == first_open:
            status = "warn" if warn and index == first_open else "current"
        elif index == 7 and approved:
            status = "current"
        else:
            status = "todo"
        steps.append({
            "key": key,
            "no": index + 1,
            "title": title,
            "status": status,
            "detail": details[index],
            "action": _SKETCH_ACTIONS[key],
        })
    return steps
