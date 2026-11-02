"""Proposal adjudication as a plugin chain.

Generation is model-proposed, platform-adjudicated: the model never gets to
emit a blueprint that the deterministic compiler trusts.  Each adjudicator is
a small, ordered contribution; the default one enforces the ontology/schema
rules, while the sanitizer is a defence-in-depth pass against layout keys the
engine would reject anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...errors import ValidationError
from .bridge import _engine
from .catalog import (
    GOALS,
    MAX_QUESTION,
    MAX_VIEWS,
    PERSPECTIVES,
    SCHEMA_VERSION,
)
from .views import table as table_view
from .views.spatial import SPATIAL_VIEW_TYPES

# Must stay aligned with the engine's Blueprint validator.  Keeping a local
# copy lets the platform prune with a reason *before* compilation.
FORBIDDEN_LAYOUT_KEYS = frozenset({
    "rect", "x", "y", "w", "h", "style", "color", "background", "width",
    "height", "css", "className", "code", "script", "javascript",
    "expression", "expressions", "html", "template",
})


@dataclass
class AdjudicationContext:
    """Everything an adjudicator may read, plus the shared pruned ledger."""

    pack: dict[str, Any]
    question: str
    ontology: Any
    ontology_version: str
    requested_focus: dict[str, Any] | None = None
    platform: Any | None = None
    pruned: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AdjudicationResult:
    blueprint: dict[str, Any]
    checks: list[dict[str, Any]]
    pruned: list[dict[str, Any]]


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


def requested_view_type(raw: Any, perspective: str) -> str:
    """Map a proposed ``display``/``chartType`` to the matching view plugin.

    Returns an empty string when the proposal does not choose a variant, so the
    caller can keep its fallback behaviour (default map, default chart, ...).
    Location variants deliberately need an explicit ``display``: absent that
    option the engine must stay on the canonical ``map``/GeoMap component.
    """
    if not isinstance(raw, dict):
        return ""
    if perspective == "location":
        return SPATIAL_VIEW_TYPES.get(str(raw.get("display") or "").strip(), "")
    if perspective == "statistics":
        if raw.get("chartType") == "table" or raw.get("display") == "table":
            return "table"
        return "chart"
    return ""


def normalize_view_options(raw: Any, obj: Any, perspective: str,
                           pruned: list[dict[str, Any]],
                           platform: Any | None = None) -> dict[str, Any]:
    """Delegate v1.1 render options to the view plugin for this perspective."""
    host = getattr(platform, "smartui", None) if platform is not None else None
    if host is not None:
        try:
            candidates = []
            for record in host.find("view", perspective=perspective,
                                    permission="view.render"):
                implementation = getattr(record, "implementation", None)
                if implementation is not None and hasattr(
                        implementation, "adjudicate_options"):
                    candidates.append((record, implementation))
            view_type = requested_view_type(raw, perspective)
            for record, implementation in candidates:
                if view_type and record.manifest.view_type == view_type:
                    return implementation.adjudicate_options(
                        raw, obj, perspective, pruned)
            if candidates and perspective != "location":
                return candidates[0][1].adjudicate_options(
                    raw, obj, perspective, pruned)
        except Exception:
            pass
    return table_view.adjudicate_options(raw, obj, perspective, pruned)


class SanitizerAdjudicator:
    """Strip engine-forbidden layout keys before the schema adjudicator."""

    id = "onto.smartui.adjudicator.sanitizer"
    version = "1.0.0"
    order = 10

    def adjudicate(self, proposal: dict[str, Any],
                   context: AdjudicationContext) -> dict[str, Any]:
        for view in proposal.get("views") or []:
            if not isinstance(view, dict):
                continue
            for key in list(view):
                if key in FORBIDDEN_LAYOUT_KEYS:
                    context.pruned.append({
                        "kind": "layout",
                        "key": key,
                        "reason": "布局坐标/样式/脚本由引擎生成，模型输出一律剪除",
                    })
                    view.pop(key, None)
        return proposal


class DefaultProposalAdjudicator:
    """The schema/ontology/action whitelist that turns a proposal into v1.0."""

    id = "onto.smartui.adjudicator.default"
    version = "1.0.0"
    order = 100

    def adjudicate(self, proposal: dict[str, Any],
                   context: AdjudicationContext) -> AdjudicationResult:
        modules = _engine()
        objects = {item["name"]: item for item in context.pack["objects"]}
        context_raw = proposal.get("context") or {}
        requested_object = str(context_raw.get("object") or "").strip()
        if requested_object not in objects:
            roots = [name for name, item in objects.items()
                     if item.get("queryable")]
            if len(roots) == 1:
                requested_object = roots[0]
            else:
                raise ValidationError(
                    f"proposal context.object={requested_object!r} is not a "
                    "workspace object")
        goal = str(context_raw.get("goal") or "overview")
        if goal not in GOALS:
            goal = "overview"

        pruned = context.pruned
        object_name = requested_object
        obj = context.ontology.object(object_name)
        if obj is None:
            raise ValidationError(f"unknown object {object_name!r}")
        if not getattr(obj, "is_query_root", False):
            root, path = modules["resolve_query_root"](context.ontology,
                                                       object_name)
            root_obj = context.ontology.object(root)
            if root_obj is not None and root != object_name:
                pruned.append({
                    "kind": "object",
                    "key": object_name,
                    "reason": (f"维度对象不能作为查询根，已解析为 {root}"
                               f"（路径 {' → '.join(path)}）"),
                })
                object_name = root
                obj = root_obj
        if not getattr(obj, "is_query_root", False):
            raise ValidationError(
                f"object {object_name!r} has no legal query root in this ontology")

        allowed_perspectives = {
            cap.perspective
            for cap in modules["derive_capabilities"](context.ontology, object_name)
        }
        workspace_pack = (context.pack.get("workspacePack")
                          if isinstance(context.pack, dict) else None) or {}
        pack_perspectives = {
            str(item) for item in (workspace_pack.get("perspectives") or [])
        }
        if pack_perspectives:
            allowed_perspectives &= pack_perspectives
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
            view["options"] = normalize_view_options(
                raw_view.get("options"), obj, perspective, pruned,
                platform=context.platform)
            views.append(view)
            if len(views) >= MAX_VIEWS:
                break
        if not views:
            raise ValidationError(
                f"proposal has no perspective supported by object "
                f"{object_name!r}")

        requested_actions = proposal.get("actions") or []
        executable = {action["key"] for action in context.pack["actions"]}
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
        if isinstance(focus_raw, dict) and focus_raw.get("key") \
                and focus_raw.get("value"):
            key = str(focus_raw["key"])
            props = {prop.name for prop in obj.properties}
            if key in props:
                focus = {"key": key, "value": str(focus_raw["value"])[:72]}
            else:
                pruned.append({"kind": "focus", "key": key,
                               "reason": "不是查询根对象的属性名，已忽略焦点"})
        if context.requested_focus:
            key = str(context.requested_focus.get("key") or "")
            value = str(context.requested_focus.get("value") or "")
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
            "ontologyVersion": context.ontology_version,
            "context": {"object": object_name, "goal": goal},
            "views": views,
            "actions": actions,
            "layout": "auto",
            "meta": {
                "generatedBy": "ai_planner",
                "sourceQuestion": context.question[:MAX_QUESTION],
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
             "status": ("warn" if pruned and any(p["kind"] == "action"
                                                 for p in pruned) else "ok"),
             "detail": ("保留 " + " / ".join(actions)) if actions else "未请求动作"},
            {"key": "scope", "label": "数据范围", "status": "ok",
             "detail": "仅使用本工作空间的本体与焦点，不含实例明细"},
        ]
        return AdjudicationResult(blueprint=blueprint, checks=checks,
                                  pruned=pruned)


class AdjudicatorChain:
    """Run ordered adjudicator contributions and stop at the first blueprint."""

    def __init__(self, adjudicators: list[Any] | None = None) -> None:
        self._items = sorted(
            adjudicators or [],
            key=lambda item: (getattr(item, "order", 100),
                              getattr(item, "id", "")))

    def run(self, proposal: dict[str, Any],
            context: AdjudicationContext) -> AdjudicationResult:
        current = proposal
        result: AdjudicationResult | None = None
        for adjudicator in self._items:
            output = adjudicator.adjudicate(current, context)
            if isinstance(output, AdjudicationResult):
                result = output
                current = result.blueprint
            elif isinstance(output, dict):
                if result is None:
                    current = output
                else:
                    result = AdjudicationResult(output, result.checks,
                                                result.pruned)
        if result is None:
            raise ValidationError("no adjudicator produced a blueprint")
        return result
