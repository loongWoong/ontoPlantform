"""Smart-UI Studio: LLM proposes a blueprint, the platform adjudicates it.

This module sits next to :mod:`smart_ui` (the deterministic adapter) and adds
the *generation* side:

    context pack (L0/L1/L2 + focus)
      -> LLM proposal (JSON only, pinned to blueprint-schema v1.0)
      -> platform adjudication (object / perspectives / actions / focus)
      -> existing deterministic compiler
      -> generation row (steps, hashes, validation) + /view for the runtime
      -> draft / publish snapshot attached to an application

Safety rules (see ``docs/smart-ui-llm-generation.md``):

* the model only ever writes a blueprint; coordinates, colors, scripts and
  expressions are still rejected by the engine's ``FORBIDDEN_LAYOUT_KEYS``;
* no instance data is put in the prompt except the user-visible focus value;
* a missing/unreachable model degrades to the deterministic planner and says
  so in the trace, instead of failing the page;
* generation jobs run in a background thread because a model call can take far
  longer than the console's 15 s request budget;
* every state change is audited and carries ``prompt_hash`` / ``program_hash``.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..errors import NotFoundError, ValidationError
from .smart_ui import (
    ENGINE_VERSION,
    ROLE_IDS,
    _actor_role,
    _engine,
    _require_roles_at_most,
    compile_smart_ui,
    compile_smart_ui_blueprint,
    smart_ui_meta,
    smart_ui_snapshot,
)

GOALS = (
    "overview", "profile", "anomaly_analysis", "trace",
    "comparison", "trend", "operation",
)
PERSPECTIVES = (
    "identity", "relationship", "timeline", "location",
    "statistics", "state", "rule", "action",
)
MAX_QUESTION = 500
MAX_PROMPT_BYTES = 16000
MAX_VIEWS = 8
SCHEMA_VERSION = "1.0"
# A generation can outlive a server restart.  Anything older than this without
# a terminal status is an orphan, not a live job (the provider timeout is 120 s).
STALE_GENERATION_SECONDS = 15 * 60
MAX_CONCURRENT_PER_WORKSPACE = 2
MAX_GENERATION_WORKERS = 4

GENERATION_KIND = "ui_generation"
DRAFT_KIND = "ui_draft"


def _blueprint_diff(parent: dict, child: dict) -> dict[str, Any]:
    """Human-readable change set between two adjudicated blueprints.

    The Studio could show a new version but not *what changed*; users had to
    diff two previews by eye.  This is deliberately presentation-oriented (a
    stable list of kind/key/before/after), never used for adjudication.
    """
    changes: list[dict[str, Any]] = []

    def scalar(kind: str, key: str, before: Any, after: Any) -> None:
        if before != after:
            changes.append({"kind": kind, "key": key,
                            "before": before, "after": after})

    p_context = parent.get("context") or {}
    c_context = child.get("context") or {}
    scalar("object", "context.object", p_context.get("object"),
           c_context.get("object"))
    scalar("goal", "context.goal", p_context.get("goal"),
           c_context.get("goal"))
    scalar("focus", "context.focus", p_context.get("focus"),
           c_context.get("focus"))

    def view_map(blueprint: dict) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for view in blueprint.get("views") or []:
            if isinstance(view, dict) and view.get("perspective"):
                result[str(view["perspective"])] = view
        return result

    p_views, c_views = view_map(parent), view_map(child)
    for key in sorted(set(p_views) | set(c_views)):
        before = p_views.get(key)
        after = c_views.get(key)
        if before is None:
            changes.append({"kind": "view", "key": key,
                            "before": None, "after": after})
        elif after is None:
            changes.append({"kind": "view", "key": key,
                            "before": before, "after": None})
        else:
            scalar("view", f"{key}.title", before.get("title"),
                   after.get("title"))
            if before.get("options") != after.get("options"):
                changes.append({
                    "kind": "option", "key": f"{key}.options",
                    "before": before.get("options") or None,
                    "after": after.get("options") or None,
                })

    p_actions = set(parent.get("actions") or [])
    c_actions = set(child.get("actions") or [])
    for key in sorted(c_actions - p_actions):
        changes.append({"kind": "action", "key": key,
                        "before": None, "after": True})
    for key in sorted(p_actions - c_actions):
        changes.append({"kind": "action", "key": key,
                        "before": True, "after": None})
    scalar("displayName", "displayName", parent.get("displayName"),
           child.get("displayName"))
    return {
        "from": parent.get("name"),
        "to": child.get("name"),
        "changed": bool(changes),
        "changes": changes,
    }


def _focus_request(raw: Any) -> dict[str, str] | None:
    """Normalize a user-supplied focus selection (object/key/value)."""
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or raw.get("identityKey") or "").strip()
    value = str(raw.get("value") or raw.get("focusValue") or "").strip()
    if not key or not value:
        return None
    return {
        "object": str(raw.get("object") or raw.get("objectType") or "")[:64],
        "key": key[:64],
        "value": value[:72],
    }

# Bounded task execution: daemon threads let a single user start unbounded
# model calls and made cancellation/recovery impossible.  The executor queues
# work while a per-workspace semaphore keeps one tenant from starving others.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=MAX_GENERATION_WORKERS,
    thread_name_prefix="smart-ui",
)
_SLOTS_LOCK = threading.Lock()
_WORKSPACE_SLOTS: dict[str, threading.BoundedSemaphore] = {}


class _GenerationCancelled(Exception):
    """Raised internally when the user cancelled a queued/running job."""

_STEPS = (
    ("context", "组装上下文包"),
    ("propose", "模型提议蓝图"),
    ("adjudicate", "平台裁决"),
    ("compile", "确定性编译"),
    ("ready", "预览就绪"),
)


def studio_enabled() -> bool:
    value = os.environ.get("ONTO_SMART_UI_STUDIO", "on").strip().lower()
    return value not in {"off", "0", "false", "no"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _usage_cost(usage: dict | None) -> dict[str, Any] | None:
    """Optional token cost estimate; disabled unless rates are configured.

    Pricing is deployment-specific, so the platform never hard-codes a provider
    price. Set ``ONTO_SMARTUI_INPUT_COST_PER_1K`` /
    ``ONTO_SMARTUI_OUTPUT_COST_PER_1K`` (and optionally
    ``ONTO_SMARTUI_COST_CURRENCY``) to enable the estimate.
    """
    if not isinstance(usage, dict):
        return None
    try:
        input_rate = float(os.environ.get(
            "ONTO_SMARTUI_INPUT_COST_PER_1K", "") or 0)
        output_rate = float(os.environ.get(
            "ONTO_SMARTUI_OUTPUT_COST_PER_1K", "") or 0)
    except ValueError:
        return None
    if input_rate <= 0 and output_rate <= 0:
        return None
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens")
                     or usage.get("output_tokens") or 0)
    amount = (prompt * input_rate + completion * output_rate) / 1000
    return {
        "amount": round(amount, 6),
        "currency": os.environ.get("ONTO_SMARTUI_COST_CURRENCY", "CNY"),
        "inputRatePer1k": input_rate,
        "outputRatePer1k": output_rate,
    }


def _duration_ms(row: dict) -> int | None:
    try:
        started = datetime.fromisoformat(str(row.get("created_at")))
        finished = datetime.fromisoformat(
            str(row.get("finished_at") or row.get("updated_at")))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return max(0, int((finished - started).total_seconds() * 1000))


def _summary(row: dict) -> dict[str, Any]:
    trace = row.get("trace") or {}
    blueprint = row.get("blueprint") or {}
    context = (blueprint.get("context") or {}) if blueprint else {}
    return {
        "id": row.get("id"),
        "workspace_id": row.get("workspace_id"),
        "question": row.get("question", ""),
        "instruction": row.get("instruction", ""),
        "status": row.get("status", "queued"),
        "mode": row.get("mode", "llm"),
        "requested_mode": row.get("requested_mode"),
        "usage": trace.get("usage"),
        "cost": (row.get("metrics") or {}).get("cost"),
        "durationMs": _duration_ms(row),
        "parent_id": row.get("parent_id"),
        "version": int(row.get("version") or 1),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "finished_at": row.get("finished_at"),
        "actor": row.get("actor"),
        "roles": list(row.get("roles") or []),
        "focus": row.get("focus"),
        "object": context.get("object") or None,
        "goal": context.get("goal") or None,
        "view_count": int(row.get("view_count") or 0),
        "program_hash": trace.get("programHash"),
        "model": trace.get("model"),
        "provider": trace.get("provider"),
        "error": row.get("error"),
        "draft_id": row.get("draft_id"),
        "published_application_id": row.get("published_application_id"),
    }


def _full(row: dict) -> dict[str, Any]:
    return {
        **_summary(row),
        "steps": list(row.get("steps") or []),
        "validation": row.get("validation") or {},
        "trace": row.get("trace") or {},
        "metrics": row.get("metrics") or {},
        "proposal": row.get("proposal"),
        "blueprint": row.get("blueprint"),
        "context": row.get("context") or {},
        "budget": row.get("budget") or {},
    }


def _save(platform, row: dict) -> None:
    row["updated_at"] = _now()
    platform.ctx.repo.put(GENERATION_KIND, row["workspace_id"], row["id"], row)


def _get(platform, workspace_id: str, generation_id: str) -> dict:
    row = platform.ctx.repo.get(GENERATION_KIND, workspace_id, generation_id)
    if row is None:
        raise NotFoundError(f"generation not found: {generation_id!r}")
    return row


def _workspace_slot(workspace_id: str) -> threading.BoundedSemaphore:
    with _SLOTS_LOCK:
        return _WORKSPACE_SLOTS.setdefault(
            workspace_id, threading.BoundedSemaphore(MAX_CONCURRENT_PER_WORKSPACE))


def _cancelled(platform, workspace_id: str, generation_id: str) -> bool:
    row = platform.ctx.repo.get(GENERATION_KIND, workspace_id, generation_id)
    return bool(row and row.get("status") == "cancelled")


def _ensure_live(platform, workspace_id: str, generation_id: str) -> None:
    if _cancelled(platform, workspace_id, generation_id):
        raise _GenerationCancelled()


def _recover_stale(platform, workspace_id: str) -> None:
    """Turn jobs orphaned by a process restart into an actionable status.

    Without this the console polls a ``running`` row forever: the worker that
    owned it died with the previous process.  This is lazy on read so it works
    for both a long-lived server and tests without a background sweeper.
    """
    import datetime as _dt

    rows = platform.ctx.repo.list(GENERATION_KIND, workspace_id)
    changed = False
    for row in rows:
        if row.get("status") not in {"queued", "running"}:
            continue
        updated = str(row.get("updated_at") or row.get("created_at") or "")
        try:
            ts = _dt.datetime.fromisoformat(updated)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
        age = (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds()
        if age <= STALE_GENERATION_SECONDS:
            continue
        row["status"] = "interrupted"
        row["error"] = "生成任务因服务重启而中断；可以直接重试"
        row["finished_at"] = _now()
        row["updated_at"] = _now()
        for step in row.get("steps") or []:
            if step.get("status") in {"pending", "running"}:
                step["status"] = "warn"
                step["detail"] = "服务重启，任务中断"
        platform.ctx.repo.put(
            GENERATION_KIND, workspace_id, str(row.get("id")), row)
        changed = True
    if changed:
        platform.ctx.audit_event(
            "system", workspace_id, "smart_ui.recover", "ui_generation",
            {"recovered": True})


def _submit_generation(platform, workspace_id: str, generation_id: str,
                       actor: str, roles: list[str], question: str,
                       instruction: str, parent: dict | None, mode: str) -> None:
    def run() -> None:
        with _workspace_slot(workspace_id):
            if _cancelled(platform, workspace_id, generation_id):
                return
            _run_generation(
                platform, workspace_id, generation_id, actor, roles, question,
                instruction, parent, mode)

    _EXECUTOR.submit(run)


def _role_ok(platform, workspace_id: str, actor: str, roles: list[str]) -> list[str]:
    actor_role = _actor_role(platform, workspace_id, actor)
    clean = [role for role in roles if role in ROLE_IDS]
    if not clean:
        raise ValidationError(f"roles must contain at least one of {list(ROLE_IDS)}")
    _require_roles_at_most(actor_role, clean)
    return clean


def _context_pack(platform, workspace_id: str, actor: str,
                  snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """A prompt-safe view of the workspace: names, counts, focus, capabilities.

    No instance records/property values are included; the only value is the
    focus the user is already looking at (needed to scope the view).
    """
    snapshot = snapshot or smart_ui_snapshot(platform, workspace_id, actor)
    ontology = snapshot["ontology"]
    meta = smart_ui_meta(platform, workspace_id, actor)
    modules = _engine()
    objects: list[dict[str, Any]] = []
    for object_name in meta.get("objectTypes") or []:
        obj = ontology.object(str(object_name))
        if obj is None:
            continue
        caps = modules["derive_capabilities"](ontology, str(object_name))
        objects.append({
            "name": obj.name,
            "displayName": obj.display_name or obj.name,
            "role": getattr(obj, "role", ""),
            # Only fact/aggregate_root objects can be a Blueprint context.object.
            # A dimension (station, lane, vehicle) is still listed with the
            # legal root the platform will resolve it to.
            "queryable": bool(getattr(obj, "is_query_root", False)),
            "resolvedQueryRoot": (
                None if getattr(obj, "is_query_root", False)
                else modules["resolve_query_root"](ontology, obj.name)[0]
            ),
            "properties": [
                {"name": prop.name, "type": prop.data_type,
                 "temporal": bool(getattr(prop, "is_temporal", False))}
                for prop in obj.properties
            ],
            "perspectives": [
                {"perspective": cap.perspective, "title": cap.title,
                 "evidence": list(cap.evidence)}
                for cap in caps
            ],
        })
    return {
        "workspace": meta.get("workspace"),
        "actorRole": meta.get("actorRole"),
        "roles": meta.get("roles"),
        "focus": meta.get("focus"),
        "queryRoots": [
            item["name"] for item in objects if item["queryable"]
        ],
        "objects": objects,
        "actions": [
            {
                "key": action.get("key"),
                "name": action.get("name"),
                "target": action.get("target"),
                "parameters": action.get("parameters") or [],
            }
            for action in (meta.get("actions") or [])
        ],
    }


def _prompt(pack: dict[str, Any], question: str,
            base_blueprint: dict | None = None) -> tuple[str, str]:
    system = (
        "你是业务智能视图规划器。根据工作空间的本体与可用能力，输出一个"
        " Blueprint v1.0 JSON。只允许引用给定对象、视角与动作，不得输出坐标、"
        "颜色、CSS、脚本、表达式或 HTML。只输出 JSON，不要解释。"
    )
    schema_hint = {
        "name": "PascalCase 应用名，3-64 字符",
        "displayName": "中文显示名，≤64 字符",
        "description": "一句话说明，≤200 字符",
        "context": {
            "object": "必须取自 objects[].name",
            "goal": "枚举: " + " | ".join(GOALS),
            "focus": {"key": "对象属性名（可选）", "value": "实例值（可选）"},
        },
        "views": [
            {"perspective": "枚举: " + " | ".join(PERSPECTIVES),
             "title": "可选中文标题",
             "options": {
                 "chartType": "可选 bar|line|pie|table；table 仅用于 statistics",
                 "display": "可选 panel|table",
                 "groupBy": "可选对象属性名",
                 "metric": {"field": "对象属性名", "agg": "sum|avg|count|min|max"},
                 "sort": {"field": "对象属性名", "direction": "asc|desc"},
                 "limit": "可选 1-200 的整数",
                 "columns": "可选对象属性名数组（table 列）",
                 "filters": [{"field": "对象属性名",
                              "op": "eq|ne|gt|gte|lt|lte|contains|in",
                              "value": "字面量或 -7d 这类相对值"}],
             }},
        ],
        "actions": ["只能取自 actions[].key"],
        "confidence": "0-1 的数字",
    }
    user = json.dumps({
        "context_pack": pack,
        "user_request": question,
        "output_schema": schema_hint,
        "constraints": [
            "views 非空且 ≤8 个，perspective 不重复",
            "只使用 objects[].perspectives 中出现的视角",
            "actions 只能选当前角色可执行的已发布能力",
            "context.object 优先取 queryRoots；若选择 dimension，平台会解析到 resolvedQueryRoot",
            "没有合适焦点时省略 focus",
            "需要列表/筛选/排序/取前 N 条时，在对应 view 的 options 中表达，不要编造新视角",
            "options 的 groupBy/sort.field/columns/filters[].field 必须是该对象属性；limit ≤ 200",
            "table 形态只用于 statistics 视角；不得输出坐标、颜色、CSS、脚本或 expressions",
            "若 context_pack.focus 存在且用户未明确要求别的实例，focus 应沿用该对象与值",
        ],
        **({"previous_blueprint": base_blueprint} if base_blueprint else {}),
    }, ensure_ascii=False)
    return system, user


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start:end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _title_of(proposal: dict[str, Any], object_name: str) -> str:
    raw = str(proposal.get("displayName") or "").strip()
    if raw:
        return raw[:64]
    return f"{object_name} 智能视图"[:64]


def _pascal_name(proposal: dict[str, Any], object_name: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", str(proposal.get("name") or ""))
    if not raw:
        raw = re.sub(r"[^A-Za-z0-9]", "", object_name) + "SmartView"
    if not raw[0].isupper():
        raw = raw[0].upper() + raw[1:]
    if len(raw) < 3:
        raw = f"View{raw}"
    return raw[:64]


def _normalize_view_options(raw: Any, obj: Any, perspective: str,
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


def _normalize_proposal(proposal: dict[str, Any],
                        pack: dict[str, Any],
                        question: str,
                        ontology: Any,
                        ontology_version: str,
                        requested_focus: dict[str, Any] | None = None,
                        ) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Turn a loose model proposal into a schema-shaped blueprint.

    Returns ``(blueprint, checks, pruned)``.  Everything the model asks for
    that the ontology does not support is pruned *with a reason*; nothing is
    invented to make an invalid proposal compile.

    A dimension object (station / lane / vehicle) is not a legal Blueprint
    ``context.object``; the engine's mandatory root-resolution step maps it to
    a fact/aggregate_root.  The platform does that here and records it, so the
    model never has to know the storage shape.
    """
    modules = _engine()
    objects = {item["name"]: item for item in pack["objects"]}
    context_raw = proposal.get("context") or {}
    requested_object = str(context_raw.get("object") or "").strip()
    if requested_object not in objects:
        # one repair attempt: if the proposal forgot the object but the pack
        # has exactly one legal root, use it; otherwise this is a hard failure
        roots = [name for name, item in objects.items() if item.get("queryable")]
        if len(roots) == 1:
            requested_object = roots[0]
        else:
            raise ValidationError(
                f"proposal context.object={requested_object!r} is not a workspace object")
    goal = str(context_raw.get("goal") or "overview")
    if goal not in GOALS:
        goal = "overview"

    pruned: list[dict[str, Any]] = []
    object_name = requested_object
    obj = ontology.object(object_name)
    if obj is None:
        raise ValidationError(f"unknown object {object_name!r}")
    if not getattr(obj, "is_query_root", False):
        root, path = modules["resolve_query_root"](ontology, object_name)
        root_obj = ontology.object(root)
        if root_obj is not None and root != object_name:
            pruned.append({
                "kind": "object",
                "key": object_name,
                "reason": f"维度对象不能作为查询根，已解析为 {root}（路径 {' → '.join(path)}）",
            })
            object_name = root
            obj = root_obj
    if not getattr(obj, "is_query_root", False):
        raise ValidationError(
            f"object {object_name!r} has no legal query root in this ontology")

    allowed_perspectives = {
        cap.perspective for cap in modules["derive_capabilities"](ontology, object_name)
    }
    views: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_view in proposal.get("views") or []:
        if isinstance(raw_view, str):
            raw_view = {"perspective": raw_view}
        if not isinstance(raw_view, dict):
            continue
        perspective = str(raw_view.get("perspective") or "").strip()
        if perspective not in PERSPECTIVES:
            pruned.append({"kind": "view", "key": perspective,
                           "reason": "词表外的视角"})
            continue
        if perspective in seen:
            continue
        if perspective not in allowed_perspectives:
            pruned.append({"kind": "view", "key": perspective,
                           "reason": "该对象的本体能力不支持此视角"})
            continue
        seen.add(perspective)
        view: dict[str, Any] = {"perspective": perspective}
        title = str(raw_view.get("title") or "").strip()
        if title:
            view["title"] = title[:64]
        view["options"] = _normalize_view_options(
            raw_view.get("options"), obj, perspective, pruned)
        views.append(view)
        if len(views) >= MAX_VIEWS:
            break
    if not views:
        raise ValidationError(
            f"proposal has no perspective supported by object {object_name!r}")

    requested_actions = proposal.get("actions") or []
    executable = {action["key"] for action in pack["actions"]}
    actions: list[str] = []
    for raw_action in requested_actions:
        key = str(raw_action or "").strip()
        if not key or key in actions:
            continue
        if key not in executable:
            pruned.append({"kind": "action", "key": key,
                           "reason": "未发布、角色不可执行或不在本工作空间"})
            continue
        actions.append(key)

    focus_raw = context_raw.get("focus") or {}
    focus = None
    if isinstance(focus_raw, dict) and focus_raw.get("key") and focus_raw.get("value"):
        key = str(focus_raw["key"])
        props = {prop.name for prop in obj.properties}
        if key in props:
            focus = {"key": key, "value": str(focus_raw["value"])[:72]}
        else:
            pruned.append({"kind": "focus", "key": key,
                           "reason": "不是查询根对象的属性名，已忽略焦点"})
    if requested_focus:
        # An explicit selector is authoritative: the model may not have echoed
        # it, but the user asked to scope this generation to that instance.
        key = str(requested_focus.get("key") or "")
        value = str(requested_focus.get("value") or "")
        if key and value and key in {prop.name for prop in obj.properties}:
            focus = {"key": key, "value": value[:72]}
        elif key:
            pruned.append({"kind": "focus", "key": key,
                           "reason": "用户选择的焦点属性不属于查询根对象"})

    confidence = proposal.get("confidence")
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 0.75

    blueprint: dict[str, Any] = {
        "name": _pascal_name(proposal, requested_object),
        "displayName": _title_of(proposal, requested_object),
        "description": str(proposal.get("description") or "")[:500],
        "schemaVersion": SCHEMA_VERSION,
        "ontologyVersion": ontology_version,
        "context": {"object": object_name, "goal": goal},
        "views": views,
        "actions": actions,
        "layout": "auto",
        "meta": {
            "generatedBy": "ai_planner",
            "sourceQuestion": question[:MAX_QUESTION],
            "intentVersion": "1.0",
            "confidence": confidence_value,
        },
    }
    if focus:
        blueprint["context"]["focus"] = focus

    object_detail = (
        object_name if object_name == requested_object
        else f"{requested_object} → 查询根 {object_name}"
    )
    checks = [
        {"key": "schema", "label": "蓝图 Schema v1.0", "status": "ok",
         "detail": "名称、版本、上下文与视角结构合法"},
        {"key": "object", "label": "对象存在且可查询", "status": "ok",
         "detail": object_detail},
        {"key": "whitelist", "label": "视角白名单", "status": "ok",
         "detail": " / ".join(view["perspective"] for view in views)},
        {"key": "actions", "label": "动作权限",
         "status": "warn" if pruned and any(p["kind"] == "action" for p in pruned) else "ok",
         "detail": ("保留 " + " / ".join(actions)) if actions else "未请求动作"},
        {"key": "scope", "label": "数据范围", "status": "ok",
         "detail": "仅使用本工作空间的本体与焦点，不含实例明细"},
    ]
    return blueprint, checks, pruned


def _step(key: str, label: str, status: str = "pending",
          detail: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "status": status,
            "detail": detail, "durationMs": None}


def _mark(row: dict, key: str, status: str, detail: str = "",
          started: float | None = None) -> None:
    for step in row["steps"]:
        if step["key"] == key:
            step["status"] = status
            step["detail"] = detail
            if status in {"ok", "warn", "err"}:
                step["finishedAt"] = _now()
                if started is not None:
                    step["durationMs"] = _ms(started)
            else:
                step["startedAt"] = _now()
            break


def start_generation(platform, workspace_id: str, actor: str,
                     question: str, roles: list[str],
                     *, parent_id: str | None = None,
                     instruction: str = "",
                     mode: str = "auto",
                     focus: dict[str, Any] | None = None) -> dict[str, Any]:
    if not studio_enabled():
        raise ValidationError("smart-UI Studio is disabled (ONTO_SMART_UI_STUDIO=off)")
    platform.ctx.require(actor, workspace_id, "editor")
    question = (question or "").strip()
    if not question:
        raise ValidationError("question must not be empty")
    if len(question) > MAX_QUESTION:
        raise ValidationError(f"question must be ≤ {MAX_QUESTION} characters")
    roles = _role_ok(platform, workspace_id, actor, roles)
    if mode not in {"auto", "llm", "deterministic"}:
        mode = "auto"
    requested_focus = _focus_request(focus)

    parent = None
    version = 1
    if parent_id:
        parent = _get(platform, workspace_id, parent_id)
        if parent.get("status") != "succeeded" or not parent.get("blueprint"):
            raise ValidationError("只能基于已生成的版本继续修改")
        version = int(parent.get("version") or 1) + 1

    generation_id = _new_id("gen")
    row: dict[str, Any] = {
        "id": generation_id,
        "workspace_id": workspace_id,
        "actor": actor,
        "question": question,
        "instruction": (instruction or "").strip()[:MAX_QUESTION],
        "roles": roles,
        "focus": requested_focus,
        "mode": "llm",
        "requested_mode": mode,
        "status": "queued",
        "parent_id": parent_id,
        "version": version,
        "created_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
        "steps": [_step(key, label) for key, label in _STEPS],
        "proposal": None,
        "blueprint": None,
        "program": None,
        "artifacts": None,
        "validation": {"ok": False, "checks": [], "pruned": []},
        "trace": {},
        "metrics": {},
        "budget": {"maxPromptBytes": MAX_PROMPT_BYTES},
        "view_count": 0,
        "error": None,
        "draft_id": None,
        "published_application_id": None,
    }
    _save(platform, row)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.generate", generation_id, {
        "question": question, "roles": roles, "version": version,
        "parent_id": parent_id, "mode": mode,
    })
    _submit_generation(
        platform, workspace_id, generation_id, actor, roles, question,
        instruction, parent, mode)
    return _full(row)


def _fallback(platform, workspace_id: str, actor: str, row: dict,
              roles: list[str], question: str, reason: str,
              started: float) -> None:
    """Deterministic plan is the safety net: never show a blank page."""
    _ensure_live(platform, row["workspace_id"], row["id"])
    _mark(row, "propose", "warn", f"模型未接续，改用确定性规划：{reason}", started)
    result = compile_smart_ui(
        platform, workspace_id, actor, question, roles, focus=row.get("focus"))
    compiled = result["compiled"]
    blueprint = compiled.get("blueprint")
    if not blueprint:
        raise ValidationError("deterministic fallback produced no blueprint")
    row["mode"] = "deterministic" if row.get("requested_mode") == "deterministic" else "fallback"
    row["status"] = "succeeded"
    row["blueprint"] = blueprint
    row["program"] = compiled.get("program")
    row["artifacts"] = compiled.get("artifacts")
    row["view_count"] = len(blueprint.get("views") or [])
    row["validation"] = {
        "ok": True,
        "checks": [
            {"key": "schema", "label": "确定性规划器", "status": "warn",
             "detail": "模型不可用，已用 L3 关键词规划 + L4 编译"},
            {"key": "object", "label": "对象存在且可查询", "status": "ok",
             "detail": str((blueprint.get("context") or {}).get("object") or "")},
            {"key": "whitelist", "label": "视角白名单", "status": "ok",
             "detail": " / ".join(
                 str(view.get("perspective")) for view in blueprint.get("views") or [])},
            {"key": "actions", "label": "动作权限", "status": "ok",
             "detail": "由确定性规划器按角色裁剪"},
            {"key": "scope", "label": "数据范围", "status": "ok",
             "detail": "同一快照的 ontology + focus"},
        ],
        "pruned": [],
        "fallbackReason": reason,
    }
    _mark(row, "adjudicate", "warn", "确定性回退：平台仍执行同一套校验", started)
    _mark(row, "compile", "ok",
          f"{row['view_count']} 个视角 · {len(compiled.get('program') or '')} 字符",
          started)
    _mark(row, "ready", "ok", "预览已就绪", started)
    row["trace"] = {
        **(row.get("trace") or {}),
        "programHash": compiled.get("programHash"),
        "engineVersion": (compiled.get("artifacts") or {}).get(
            "engineVersion", ENGINE_VERSION),
        "ontologyVersion": (compiled.get("artifacts") or {}).get(
            "ontology", {}).get("ontologyVersion"),
        "fallback": True,
    }
    row["finished_at"] = _now()
    _ensure_live(platform, row["workspace_id"], row["id"])
    _save(platform, row)


def _run_generation(platform, workspace_id: str, generation_id: str,
                    actor: str, roles: list[str], question: str,
                    instruction: str, parent: dict | None, mode: str) -> None:
    started = time.perf_counter()
    row = _get(platform, workspace_id, generation_id)
    try:
        if row.get("status") == "cancelled":
            return
        row["status"] = "running"
        _mark(row, "context", "running")
        _save(platform, row)

        snapshot = smart_ui_snapshot(
            platform, workspace_id, actor, focus=row.get("focus"))
        _ensure_live(platform, workspace_id, generation_id)
        pack = _context_pack(platform, workspace_id, actor, snapshot=snapshot)
        meta = smart_ui_meta(platform, workspace_id, actor)
        if meta.get("ready") is False:
            raise ValidationError(str(meta.get("reason") or "workspace has no instances"))
        prompt_bytes = len(json.dumps(pack, ensure_ascii=False))
        row["context"] = {
            "workspace": pack["workspace"],
            "actorRole": pack["actorRole"],
            "focus": pack["focus"],
            "objectTypes": [item["name"] for item in pack["objects"]],
            "actionKeys": [item["key"] for item in pack["actions"]],
        }
        row["budget"] = {
            "maxPromptBytes": MAX_PROMPT_BYTES,
            "contextBytes": prompt_bytes,
        }
        _mark(row, "context", "ok",
              f"{len(pack['objects'])} 个对象 · {len(pack['actions'])} 个可执行动作", started)
        _save(platform, row)

        base_blueprint = (parent or {}).get("blueprint")
        if mode == "deterministic":
            _ensure_live(platform, workspace_id, generation_id)
            _fallback(platform, workspace_id, actor, row, roles, question,
                      "请求指定确定性模式", started)
            return

        provider = platform.ctx.llm_for(workspace_id)
        if provider is None:
            _ensure_live(platform, workspace_id, generation_id)
            _fallback(platform, workspace_id, actor, row, roles, question,
                      "当前工作空间未配置模型", started)
            return

        model_name = str(getattr(provider, "model", "") or type(provider).__name__)
        row["trace"] = {"model": model_name, "provider": type(provider).__name__}
        _save(platform, row)

        from ..ports.llm import LLMMessage

        system, user = _prompt(pack, question, base_blueprint)
        if instruction:
            user += f"\n修改要求：{instruction}"
        prompt_text = system + "\n" + user
        row["trace"].update({
            "promptHash": _sha(prompt_text),
            "promptBytes": len(prompt_text.encode("utf-8")),
        })
        if row["trace"]["promptBytes"] > MAX_PROMPT_BYTES:
            _ensure_live(platform, workspace_id, generation_id)
            _fallback(
                platform, workspace_id, actor, row, roles, question,
                f"上下文过大（{row['trace']['promptBytes']} > {MAX_PROMPT_BYTES} 字节）",
                started)
            return
        row["budget"]["promptBytes"] = row["trace"]["promptBytes"]
        _mark(row, "propose", "running")
        _save(platform, row)

        proposal: dict[str, Any] | None = None
        last_error = ""
        for attempt in range(2):
            messages = [LLMMessage("system", system), LLMMessage("user", user)]
            if attempt == 1:
                messages.append(LLMMessage(
                    "user", "上一次输出不是合法 JSON。只输出一个 JSON 对象，不要代码块。"))
            try:
                response = provider.complete(messages)
            except Exception as exc:  # transport failure -> deterministic fallback
                _ensure_live(platform, workspace_id, generation_id)
                reason = str(exc) or type(exc).__name__
                if mode == "llm":
                    raise ValidationError(
                        f"模型调用失败，且当前为显式模型模式：{reason}") from exc
                _fallback(platform, workspace_id, actor, row, roles, question,
                          f"模型不可用（{reason}）", started)
                return
            content = getattr(response, "content", "") or ""
            usage = getattr(response, "usage", None)
            if isinstance(usage, dict):
                row["trace"]["usage"] = dict(usage)
            row["trace"]["responseBytes"] = len(content.encode("utf-8"))
            proposal = _extract_json(content)
            if proposal is not None:
                break
            last_error = "模型输出不是合法 JSON"
            if content:
                row["trace"]["rawPreview"] = content[:2000]
        if proposal is None:
            _save(platform, row)
            raise ValidationError(f"{last_error}（已重试 1 次）")

        _ensure_live(platform, workspace_id, generation_id)
        row["proposal"] = proposal
        _mark(row, "propose", "ok", f"{model_name} · {row['trace'].get('responseBytes', 0)} 字节", started)
        _mark(row, "adjudicate", "running")
        _save(platform, row)

        ontology = snapshot["ontology"]
        blueprint, checks, pruned = _normalize_proposal(
            proposal, pack, question, ontology, ontology.ontology_version,
            requested_focus=row.get("focus"))
        row["blueprint"] = blueprint
        row["view_count"] = len(blueprint["views"])
        row["validation"] = {"ok": True, "checks": checks, "pruned": pruned}
        _mark(row, "adjudicate", "ok",
              f"{len(blueprint['views'])} 个视角 · 剪除 {len(pruned)} 项", started)
        _mark(row, "compile", "running")
        _save(platform, row)

        compiled = compile_smart_ui_blueprint(
            platform, workspace_id, actor, question, roles, blueprint,
            snapshot=snapshot, pruned=pruned,
        )["compiled"]
        row["trace"].update({
            "programHash": compiled.get("programHash"),
            "engineVersion": (compiled.get("artifacts") or {}).get(
                "engineVersion", ENGINE_VERSION),
            "ontologyVersion": ontology.ontology_version,
        })
        row["program"] = compiled.get("program")
        row["artifacts"] = compiled.get("artifacts")
        row["mode"] = "llm"
        row["status"] = "succeeded"
        row["error"] = None
        _mark(row, "compile", "ok",
              f"{len(blueprint['views'])} 个视角 · {len(compiled.get('program') or '')} 字符",
              started)
        _mark(row, "ready", "ok", "预览已就绪", started)
        row["finished_at"] = _now()
        row["metrics"] = {"totalMs": _ms(started),
                          "compileMs": compiled.get("compileMs")}
        if row["trace"].get("usage"):
            row["metrics"]["usage"] = row["trace"]["usage"]
            cost = _usage_cost(row["trace"]["usage"])
            if cost is not None:
                row["metrics"]["cost"] = cost
        _ensure_live(platform, workspace_id, generation_id)
        _save(platform, row)
        platform.ctx.audit_event(actor, workspace_id, "smart_ui.generate", generation_id, {
            "status": "succeeded", "mode": "llm",
            "programHash": row["trace"].get("programHash"),
            "promptHash": row["trace"].get("promptHash"),
            "views": row["view_count"],
        })
    except _GenerationCancelled:
        return
    except Exception as exc:  # keep the job row inspectable, never crash the thread
        try:
            current = _get(platform, workspace_id, generation_id)
            if current.get("status") == "cancelled":
                return
            # Auto mode promises a usable result: a malformed/invalid proposal
            # gets the same deterministic safety net as a transport failure.
            if current.get("requested_mode") == "auto" and current.get("status") in {
                    "queued", "running"}:
                try:
                    _fallback(
                        platform, workspace_id, actor, current, roles, question,
                        f"模型或编译失败（{str(exc)[:180]}）", started)
                    return
                except _GenerationCancelled:
                    return
                except Exception:
                    pass
            current["status"] = "failed"
            current["error"] = str(exc)
            current["finished_at"] = _now()
            current["metrics"] = {"totalMs": _ms(started)}
            current["validation"] = {
                **(current.get("validation") or {}),
                "ok": False,
                "error": str(exc),
            }
            for step in current["steps"]:
                if step["status"] in {"running", "pending"}:
                    _mark(current, step["key"], "err", str(exc)[:160], started)
                    break
            _save(platform, current)
            platform.ctx.audit_event(
                actor, workspace_id, "smart_ui.generate", generation_id,
                {"status": "failed", "error": str(exc)},
            )
        except _GenerationCancelled:
            return
        except Exception:
            pass


def get_generation(platform, workspace_id: str, actor: str,
                   generation_id: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "viewer")
    _recover_stale(platform, workspace_id)
    row = _get(platform, workspace_id, generation_id)
    result = _full(row)
    result["lineage"] = _lineage(platform, workspace_id, row)
    parent_id = row.get("parent_id")
    if parent_id:
        parent = platform.ctx.repo.get(GENERATION_KIND, workspace_id, parent_id)
        if parent is not None and parent.get("blueprint") and row.get("blueprint"):
            result["diff"] = _blueprint_diff(
                parent["blueprint"], row["blueprint"])
    return result


def _lineage(platform, workspace_id: str, row: dict) -> list[dict[str, Any]]:
    """Ancestor summaries, newest first, bounded against a broken/cyclic chain."""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = row
    while current is not None and len(chain) < 20:
        generation_id = str(current.get("id") or "")
        if not generation_id or generation_id in seen:
            break
        seen.add(generation_id)
        chain.append(_summary(current))
        parent_id = current.get("parent_id")
        current = (platform.ctx.repo.get(GENERATION_KIND, workspace_id, parent_id)
                   if parent_id else None)
    return chain


def cancel_generation(platform, workspace_id: str, actor: str,
                      generation_id: str) -> dict[str, Any]:
    """Cancel a queued/running generation; terminal jobs are a no-op.

    The worker may be blocked inside a model call, so this only records intent
    and the terminal status.  The worker checks the marker between steps and
    discards its result, never overwriting the cancelled row.
    """
    platform.ctx.require(actor, workspace_id, "editor")
    row = _get(platform, workspace_id, generation_id)
    if row.get("status") in {"succeeded", "failed", "cancelled", "interrupted"}:
        return _full(row)
    row["status"] = "cancelled"
    row["cancel_requested"] = True
    row["error"] = "已取消"
    row["finished_at"] = _now()
    for step in row.get("steps") or []:
        if step.get("status") in {"pending", "running"}:
            step["status"] = "warn"
            step["detail"] = "用户取消"
    _save(platform, row)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.cancel", generation_id, {})
    return _full(row)


def _prune_generations(platform, workspace_id: str) -> int:
    """Optional retention: drop old, unowned generation snapshots.

    Disabled by default (``ONTO_SMARTUI_HISTORY_RETENTION_DAYS=0``). Published
    drafts/applications and live jobs are never pruned.
    """
    try:
        days = float(os.environ.get(
            "ONTO_SMARTUI_HISTORY_RETENTION_DAYS", "0") or 0)
    except ValueError:
        return 0
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    drafts = _drafts(platform, workspace_id)
    published = {draft.get("generation_id")
                 for draft in drafts
                 if draft.get("status") == "published"}
    removed = 0
    for row in platform.ctx.repo.list(GENERATION_KIND, workspace_id):
        if removed >= 200:
            break
        if row.get("id") in published:
            continue
        if row.get("status") in {"queued", "running"}:
            continue
        try:
            created = datetime.fromisoformat(str(row.get("created_at")))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created >= cutoff:
            continue
        platform.ctx.repo.delete(GENERATION_KIND, workspace_id,
                                 str(row.get("id")))
        for draft in drafts:
            if (draft.get("generation_id") == row.get("id")
                    and draft.get("status") != "published"):
                platform.ctx.repo.delete(DRAFT_KIND, workspace_id,
                                         str(draft.get("id")))
        removed += 1
    return removed


def list_generations(platform, workspace_id: str, actor: str,
                     limit: int = 30, offset: int = 0,
                     query: str = "", status: str = "",
                     object_type: str = "", mode: str = "") -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "viewer")
    _recover_stale(platform, workspace_id)
    _prune_generations(platform, workspace_id)
    rows = platform.ctx.repo.list(GENERATION_KIND, workspace_id)
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    needle = str(query or "").strip().lower()
    wanted_status = str(status or "").strip()
    wanted_object = str(object_type or "").strip()
    wanted_mode = str(mode or "").strip()

    def matches(row: dict) -> bool:
        if wanted_status and row.get("status") != wanted_status:
            return False
        if wanted_mode and row.get("mode") != wanted_mode:
            return False
        if wanted_object:
            row_object = str(
                (row.get("blueprint") or {}).get("context", {}).get("object") or "")
            if row_object != wanted_object:
                return False
        if needle:
            haystack = " ".join(str(row.get(key) or "") for key in (
                "question", "instruction", "display_name", "name", "id"))
            if needle not in haystack.lower():
                return False
        return True

    filtered = [row for row in rows if matches(row)]
    start = max(0, int(offset or 0))
    size = max(1, min(100, int(limit or 30)))
    items = [_summary(row) for row in filtered[start:start + size]]
    return {
        "items": items,
        "total": len(filtered),
        "offset": start,
        "limit": size,
        "all": len(rows),
    }


def delete_generation(platform, workspace_id: str, actor: str,
                      generation_id: str) -> dict[str, Any]:
    """Delete an unpublished generation and its (unpublished) draft snapshot.

    A published draft / application artifact owns the generation id, so deleting
    it would break the application AI view; that case is refused.
    """
    platform.ctx.require(actor, workspace_id, "editor")
    row = _get(platform, workspace_id, generation_id)
    if row.get("status") in {"queued", "running"}:
        raise ValidationError("generation is still running; cancel it first")
    drafts = [draft for draft in _drafts(platform, workspace_id)
              if draft.get("generation_id") == generation_id]
    if any(draft.get("status") == "published" for draft in drafts):
        raise ValidationError(
            "generation is referenced by a published draft; archive the application first")
    for draft in drafts:
        platform.ctx.repo.delete(DRAFT_KIND, workspace_id, str(draft.get("id")))
    platform.ctx.repo.delete(GENERATION_KIND, workspace_id, generation_id)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.delete", generation_id,
                             {"drafts": len(drafts)})
    return {"deleted": generation_id, "drafts": len(drafts)}


def generation_view(platform, workspace_id: str, actor: str,
                    generation_id: str) -> dict[str, Any]:
    """Recompile the stored blueprint against the current snapshot.

    The preview therefore shows live platform data, while the *layout* stays
    exactly the accepted version (no re-planning on refresh).
    """
    platform.ctx.require(actor, workspace_id, "viewer")
    _recover_stale(platform, workspace_id)
    row = _get(platform, workspace_id, generation_id)
    if row.get("status") != "succeeded" or not row.get("blueprint"):
        raise ValidationError("generation is not ready yet")
    roles = _role_ok(platform, workspace_id, actor, list(row.get("roles") or []))
    result = compile_smart_ui_blueprint(
        platform, workspace_id, actor, str(row.get("question") or ""),
        roles, row["blueprint"],
        pruned=(row.get("validation") or {}).get("pruned") or [],
    )
    return {
        **result,
        "generation": _summary(row),
    }


def refine_generation(platform, workspace_id: str, actor: str,
                      generation_id: str, prompt: str,
                      focus: dict[str, Any] | None = None) -> dict[str, Any]:
    parent = _get(platform, workspace_id, generation_id)
    return start_generation(
        platform, workspace_id, actor,
        parent.get("question") or "",
        list(parent.get("roles") or []),
        parent_id=generation_id,
        instruction=prompt,
        mode="auto",
        focus=focus if isinstance(focus, dict) else parent.get("focus"),
    )


def _drafts(platform, workspace_id: str) -> list[dict]:
    return platform.ctx.repo.list(DRAFT_KIND, workspace_id)


def accept_generation(platform, workspace_id: str, actor: str,
                      generation_id: str, name: str = "") -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    row = _get(platform, workspace_id, generation_id)
    if row.get("status") != "succeeded" or not row.get("blueprint"):
        raise ValidationError("generation is not ready yet")
    trace = row.get("trace") or {}
    drafts = _drafts(platform, workspace_id)
    draft_id = row.get("draft_id")
    draft = next((item for item in drafts if item.get("id") == draft_id), None)
    if draft is None:
        version = max([int(item.get("version") or 1) for item in drafts] or [0]) + 1
        draft = {
            "id": _new_id("dft"),
            "workspace_id": workspace_id,
            "generation_id": generation_id,
            "name": (name or "").strip() or str(
                (row.get("blueprint") or {}).get("displayName") or "智能视图"),
            "version": version,
            "program_hash": trace.get("programHash"),
            "blueprint": row["blueprint"],
            "program": row.get("program"),
            "question": row.get("question"),
            "roles": list(row.get("roles") or []),
            "ontology_version": trace.get("ontologyVersion"),
            "status": "draft",
            "created_at": _now(),
            "created_by": actor,
            "updated_at": _now(),
            "published_at": None,
            "application_id": None,
        }
        drafts.append(draft)
        row["draft_id"] = draft["id"]
        _save(platform, row)
    else:
        if draft.get("status") == "published":
            raise ValidationError(
                "this version is already published; generate a new version to change it")
        if name:
            draft["name"] = name.strip()[:64]
        draft["blueprint"] = row["blueprint"]
        draft["program"] = row.get("program")
        draft["program_hash"] = trace.get("programHash")
        draft["question"] = row.get("question")
        draft["roles"] = list(row.get("roles") or [])
        draft["ontology_version"] = trace.get("ontologyVersion")
        draft["updated_at"] = _now()
    platform.ctx.repo.put(DRAFT_KIND, workspace_id, draft["id"], draft)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.accept", draft["id"], {
        "generation_id": generation_id, "version": draft["version"],
        "program_hash": draft.get("program_hash"),
    })
    return draft


def list_drafts(platform, workspace_id: str, actor: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "viewer")
    drafts = _drafts(platform, workspace_id)
    drafts.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return {"items": drafts, "total": len(drafts)}


def _get_draft(platform, workspace_id: str, draft_id: str) -> dict:
    draft = platform.ctx.repo.get(DRAFT_KIND, workspace_id, draft_id)
    if draft is None:
        raise NotFoundError(f"draft not found: {draft_id!r}")
    return draft


def update_draft(platform, workspace_id: str, actor: str, draft_id: str,
                 name: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    draft = _get_draft(platform, workspace_id, draft_id)
    clean = (name or "").strip()[:64]
    if not clean:
        raise ValidationError("draft name must not be empty")
    draft["name"] = clean
    draft["updated_at"] = _now()
    platform.ctx.repo.put(DRAFT_KIND, workspace_id, draft_id, draft)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.draft.rename",
                             draft_id, {"name": clean})
    return draft


def delete_draft(platform, workspace_id: str, actor: str,
                 draft_id: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    draft = _get_draft(platform, workspace_id, draft_id)
    if draft.get("status") == "published":
        raise ValidationError("published drafts are part of an application contract")
    platform.ctx.repo.delete(DRAFT_KIND, workspace_id, draft_id)
    generation = platform.ctx.repo.get(
        GENERATION_KIND, workspace_id, str(draft.get("generation_id") or ""))
    if generation is not None and generation.get("draft_id") == draft_id:
        generation["draft_id"] = None
        _save(platform, generation)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.draft.delete",
                             draft_id, {})
    return {"id": draft_id, "deleted": True}


def publish_generation(platform, workspace_id: str, actor: str,
                       generation_id: str, *, name: str = "",
                       application_id: str = "") -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    draft = accept_generation(platform, workspace_id, actor, generation_id, name=name)
    if draft.get("status") == "published":
        raise ValidationError("this draft is already published")

    app_id = (application_id or "").strip()
    if app_id:
        app = platform.applications.get(workspace_id, app_id, actor)
        if app.get("lifecycle") == "published":
            raise ValidationError(
                "published applications are immutable; clone it into a draft first")
    else:
        app = platform.applications.create(
            workspace_id, actor,
            (name or draft.get("name") or "智能视图")[:64],
            description=f"由智能视图生成台发布（{generation_id}）",
        )
        app_id = str(app["id"])

    source_row = _get(platform, workspace_id, generation_id)
    source_trace = source_row.get("trace") or {}
    artifact = {
        "generation_id": generation_id,
        "draft_id": draft["id"],
        "version": draft["version"],
        "program_hash": draft.get("program_hash"),
        "published_at": _now(),
        "published_by": actor,
        "name": draft.get("name"),
        # The artifact is the stable published contract.  Keeping the
        # adjudicated blueprint (not just a generation id) lets the application
        # render after the generation record is gone, and lets an engineer see
        # exactly what was published.  The program is kept for reproducibility;
        # the live view is still recompiled from the blueprint so data stays
        # fresh and permissions are re-checked for the current viewer.
        "blueprint": draft.get("blueprint"),
        "program": draft.get("program"),
        "question": draft.get("question"),
        "roles": list(draft.get("roles") or []),
        "ontology_version": source_trace.get("ontologyVersion"),
        "engine_version": source_trace.get("engineVersion"),
    }
    if not artifact["blueprint"]:
        raise ValidationError("published draft has no blueprint")
    platform.applications.attach_ui_artifact(workspace_id, app_id, actor, artifact)
    draft["status"] = "published"
    draft["application_id"] = app_id
    draft["published_at"] = artifact["published_at"]
    draft["updated_at"] = _now()
    platform.ctx.repo.put(DRAFT_KIND, workspace_id, draft["id"], draft)
    row = _get(platform, workspace_id, generation_id)
    row["published_application_id"] = app_id
    _save(platform, row)
    platform.ctx.audit_event(actor, workspace_id, "smart_ui.publish", draft["id"], {
        "generation_id": generation_id,
        "application_id": app_id,
        "version": draft["version"],
        "program_hash": draft.get("program_hash"),
    })
    return {
        "draft": draft,
        "application": platform.applications.get(workspace_id, app_id, actor),
        "artifact": artifact,
    }


def application_ui_view(platform, workspace_id: str, actor: str,
                        application_id: str) -> dict[str, Any]:
    """Render a published AI artifact from the application, not the generation.

    The application is the serving contract.  This endpoint intentionally reads
    ``ui_artifact.blueprint`` first and only falls back to the old
    generation-id-only artifact for already-published rows.  New publishes keep
    enough information to survive generation cleanup.
    """
    platform.ctx.require(actor, workspace_id, "viewer")
    app = platform.applications.get(workspace_id, application_id, actor)
    artifact = app.get("ui_artifact") or {}
    if not isinstance(artifact, dict) or not artifact:
        raise NotFoundError("application has no AI-generated view")

    blueprint = artifact.get("blueprint")
    if not isinstance(blueprint, dict):
        # Backward compatibility for artifacts published before the blueprint
        # was stored.  They still render while the generation row exists.
        generation_id = str(artifact.get("generation_id") or "")
        if generation_id:
            row = _get(platform, workspace_id, generation_id)
            blueprint = row.get("blueprint")
            if blueprint and not artifact.get("question"):
                artifact = {**artifact, "question": row.get("question"),
                            "roles": row.get("roles") or []}
        if not isinstance(blueprint, dict):
            raise ValidationError(
                "this published AI artifact predates snapshots; republish it")

    question = str(artifact.get("question") or (blueprint.get("meta") or {}).get(
        "sourceQuestion") or "AI 生成视图")
    roles = _role_ok(
        platform, workspace_id, actor, list(artifact.get("roles") or ["owner"]))
    result = compile_smart_ui_blueprint(
        platform, workspace_id, actor, question, roles, blueprint,
        pruned=(artifact.get("validation") or {}).get("pruned") or [],
    )
    return {
        **result,
        "artifact": {
            "generation_id": artifact.get("generation_id"),
            "draft_id": artifact.get("draft_id"),
            "version": artifact.get("version"),
            "program_hash": artifact.get("program_hash"),
            "name": artifact.get("name"),
            "published_at": artifact.get("published_at"),
            "published_by": artifact.get("published_by"),
        },
        "application": {
            "id": app.get("id"), "name": app.get("name"),
            "workspace_id": app.get("workspace_id"),
        },
    }
