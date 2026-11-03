"""Rule-pack storage, validation, dry-run and governance.

The engine owns evaluation and stays pure.  This module owns the platform
concerns: workspace-scoped persistence, versioning, publish/rollback, audit
events and dry-run.  A stored pack is data only; it can never add code.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from ...errors import NotFoundError, PlatformError, ValidationError
from .bridge import compile_smart_ui
from .packs import (
    WorkspacePack,
    set_workspace_rule_pack,
    workspace_rule_pack_id,
)

RULE_PACK_KIND = "smartui_rule_pack"
DEFAULT_RULE_PACK_ID = "onto.rules.default"


def _engine_modules() -> dict[str, Any]:
    from .bridge import _engine

    return _engine()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return "rp_" + uuid.uuid4().hex[:16]


def default_pack_dict() -> dict[str, Any]:
    """Return a deep copy of the engine's built-in default pack."""
    planner = _engine_modules()["planner"]
    return copy.deepcopy(planner.DEFAULT_RULE_PACK.to_dict())


def _rules_module():
    return _engine_modules()["rules"]


def _engine_pack(payload: Any):
    return _rules_module().RulePack.from_dict(payload)


def _pack_hash(pack: Any) -> str:
    return str(pack.hash)


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "name": record.get("name") or record.get("id"),
        "version": record.get("version") or "1.0.0",
        "status": record.get("status") or "draft",
        "description": record.get("description") or "",
        "ontologyProfile": record.get("ontology_profile") or "",
        "hash": record.get("hash") or "",
        "ruleCount": len(record.get("rules") or []),
        "source": record.get("source") or "custom",
        "builtin": bool(record.get("builtin")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "published_at": record.get("published_at"),
        "created_by": record.get("created_by"),
        "published_by": record.get("published_by"),
    }


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **_summary(record),
        "rules": copy.deepcopy(record.get("rules") or []),
        "budget": copy.deepcopy(record.get("budget") or {"maxViews": 12}),
        "workspace_id": record.get("workspace_id"),
        "parent_id": record.get("parent_id"),
    }


def _builtin_record() -> dict[str, Any]:
    payload = default_pack_dict()
    pack = _engine_pack(payload)
    return {
        **payload,
        "id": DEFAULT_RULE_PACK_ID,
        "name": payload.get("name") or "默认规则包",
        "version": str(payload.get("version") or "1.0.0"),
        "status": "builtin",
        "builtin": True,
        "hash": _pack_hash(pack),
        "source": "builtin",
        "rules": payload.get("rules") or [],
        "budget": payload.get("budget") or {"maxViews": 12},
    }


def _stored(platform: Any, workspace_id: str, pack_id: str) -> dict[str, Any] | None:
    return platform.ctx.repo.get(RULE_PACK_KIND, workspace_id, pack_id)


def _list_stored(platform: Any, workspace_id: str) -> list[dict[str, Any]]:
    rows = platform.ctx.repo.list(RULE_PACK_KIND, workspace_id)
    return sorted(
        rows,
        key=lambda row: str(row.get("updated_at") or ""),
        reverse=True,
    )


def get_rule_pack(platform: Any, workspace_id: str, actor: str,
                  pack_id: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "viewer")
    wanted = str(pack_id or "").strip()
    if not wanted or wanted == DEFAULT_RULE_PACK_ID:
        return _public(_builtin_record())
    row = _stored(platform, workspace_id, wanted)
    if row is None:
        raise NotFoundError(f"rule pack not found: {wanted!r}")
    return _public(row)


def list_rule_packs(platform: Any, workspace_id: str, actor: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "viewer")
    builtin = _builtin_record()
    items = [_summary(builtin)]
    items.extend(_summary(row) for row in _list_stored(platform, workspace_id))
    return {"items": items, "total": len(items),
            "defaultRulePackId": DEFAULT_RULE_PACK_ID,
            "boundRulePackId": workspace_rule_pack_id(
                platform, workspace_id, actor)}


# ---------------------------------------------------------------------------
# validation


def _walk_condition(node: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        yield node
        if key in {"all", "any"} and isinstance(value, list):
            for child in value:
                yield from _walk_condition(child)
        elif key == "not":
            yield from _walk_condition(value)


def _rule_perspectives(rule: Any) -> list[str]:
    """Collect perspective names referenced by a rule's ``then`` block."""
    found: list[str] = []
    for key in ("goalViews", "removeView"):
        raw = rule.then.get(key)
        if isinstance(raw, str):
            found.append(raw)
        elif isinstance(raw, list):
            found.extend(str(item) for item in raw)
    for key in ("addView", "boostView", "setViewOptions", "setTitle"):
        raw = rule.then.get(key)
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, str):
                found.append(item)
            elif isinstance(item, dict) and item.get("perspective"):
                found.append(str(item["perspective"]))
    return [item for item in found if item]


def check_rule_pack(payload: Any,
                    ontology: Any | None = None) -> tuple[Any | None, list[dict[str, Any]]]:
    """Structural + semantic validation.  Never raises for bad user data."""
    issues: list[dict[str, Any]] = []
    try:
        pack = _engine_pack(payload)
    except Exception as exc:  # RulePackError / ValueError
        return None, [{
            "code": "E_RULE_PACK",
            "ruleId": "",
            "message": str(exc),
        }]
    modules = _engine_modules()
    valid_perspectives = set(modules["planner"].PERSPECTIVE_VIEW)
    valid_goals = set(modules["planner"].GOAL_LABELS)
    object_names: set[str] = set()
    if ontology is not None:
        object_names = {str(obj.name) for obj in getattr(ontology, "objects", ())}

    if not pack.rules:
        issues.append({
            "code": "E_RULE_EMPTY",
            "ruleId": "",
            "message": "规则包至少需要一条规则；可从内置默认包复制",
        })

    for rule in pack.rules:
        for node in _walk_condition(rule.when):
            for key, value in node.items():
                if key in {"goal.in", "goal.notIn"}:
                    for goal in value if isinstance(value, list) else [value]:
                        if str(goal) not in valid_goals:
                            issues.append({
                                "code": "E_RULE_UNKNOWN_GOAL",
                                "ruleId": rule.id,
                                "message": f"未知目标 {goal!r}",
                            })
                elif key in {"capability.exists", "capability.missing",
                             "capability.allOf"}:
                    values = value if isinstance(value, list) else [value]
                    for perspective in values:
                        if str(perspective) not in valid_perspectives:
                            issues.append({
                                "code": "E_RULE_UNKNOWN_PERSPECTIVE",
                                "ruleId": rule.id,
                                "message": f"未知视角 {perspective!r}",
                            })
                elif key == "object.in" and object_names:
                    for name in value if isinstance(value, list) else [value]:
                        if str(name) not in object_names:
                            issues.append({
                                "code": "E_RULE_UNKNOWN_OBJECT",
                                "ruleId": rule.id,
                                "message": f"当前本体没有对象 {name!r}",
                            })
        if "setGoal" in rule.then and str(rule.then["setGoal"]) not in valid_goals:
            issues.append({
                "code": "E_RULE_UNKNOWN_GOAL",
                "ruleId": rule.id,
                "message": f"未知目标 {rule.then['setGoal']!r}",
            })
        for perspective in _rule_perspectives(rule):
            if perspective not in valid_perspectives:
                issues.append({
                    "code": "E_RULE_UNKNOWN_PERSPECTIVE",
                    "ruleId": rule.id,
                    "message": f"未知视角 {perspective!r}",
                })
    return pack, issues


def _payload_from_body(body: dict[str, Any],
                       base: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(base or {})
    if isinstance(body.get("pack"), dict):
        payload.update(copy.deepcopy(body["pack"]))
    for key in ("id", "name", "version", "description", "ontologyProfile",
                "ontology_profile", "budget", "rules", "source"):
        if key in body:
            value = body[key]
            if key == "ontology_profile":
                key = "ontologyProfile"
            payload[key] = value
    return payload


def validate_rule_pack(platform: Any, workspace_id: str, actor: str,
                       payload: Any) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "viewer")
    pack, issues = check_rule_pack(payload)
    return {
        "ok": not issues,
        "issues": issues,
        "hash": _pack_hash(pack) if pack is not None else "",
        "pack": pack.to_dict() if pack is not None else None,
    }


# ---------------------------------------------------------------------------
# CRUD / lifecycle


def create_rule_pack(platform: Any, workspace_id: str, actor: str,
                     body: dict[str, Any]) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    base: dict[str, Any] | None = None
    base_id = str(body.get("baseId") or body.get("base_id") or "").strip()
    if base_id:
        if base_id == DEFAULT_RULE_PACK_ID:
            base = default_pack_dict()
        else:
            row = _stored(platform, workspace_id, base_id)
            if row is None:
                raise NotFoundError(f"base rule pack not found: {base_id!r}")
            base = _public(row)
    payload = _payload_from_body(body, base)
    payload.setdefault("name", "未命名规则包")
    payload["id"] = _new_id()
    payload.setdefault("version", "1.0.0")
    pack, issues = check_rule_pack(payload)
    if issues or pack is None:
        raise ValidationError(
            "rule pack validation failed: "
            + "; ".join(issue["message"] for issue in issues[:3]))
    row = {
        **pack.to_dict(),
        "workspace_id": workspace_id,
        "status": "draft",
        "hash": _pack_hash(pack),
        "source": "custom",
        "builtin": False,
        "created_at": _now(),
        "updated_at": _now(),
        "created_by": actor,
        "published_at": None,
        "published_by": None,
        "parent_id": base_id or None,
    }
    platform.ctx.repo.put(RULE_PACK_KIND, workspace_id, row["id"], row)
    platform.ctx.audit_event(
        actor, workspace_id, "smart_ui.rule_pack.create", row["id"],
        {"name": row.get("name"), "hash": row["hash"],
         "ruleCount": len(row.get("rules") or [])})
    return _public(row)


def update_rule_pack(platform: Any, workspace_id: str, actor: str,
                     pack_id: str, body: dict[str, Any]) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    if pack_id == DEFAULT_RULE_PACK_ID:
        raise ValidationError("builtin rule pack is immutable; clone it first")
    row = _stored(platform, workspace_id, pack_id)
    if row is None:
        raise NotFoundError(f"rule pack not found: {pack_id!r}")
    if row.get("status") == "published":
        raise ValidationError(
            "published rule pack is immutable; clone it to create a new version")
    payload = _payload_from_body(body, _public(row))
    payload["id"] = pack_id
    pack, issues = check_rule_pack(payload)
    if issues or pack is None:
        raise ValidationError(
            "rule pack validation failed: "
            + "; ".join(issue["message"] for issue in issues[:3]))
    updated = {
        **row,
        **pack.to_dict(),
        "id": pack_id,
        "hash": _pack_hash(pack),
        "status": row.get("status") or "draft",
        "updated_at": _now(),
        "updated_by": actor,
    }
    platform.ctx.repo.put(RULE_PACK_KIND, workspace_id, pack_id, updated)
    platform.ctx.audit_event(
        actor, workspace_id, "smart_ui.rule_pack.update", pack_id,
        {"hash": updated["hash"]})
    return _public(updated)


def clone_rule_pack(platform: Any, workspace_id: str, actor: str,
                    pack_id: str, name: str = "") -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    source = get_rule_pack(platform, workspace_id, actor, pack_id)
    body = {
        "name": name or f"{source.get('name') or pack_id} 副本",
        "description": source.get("description") or "",
        "rules": source.get("rules") or [],
        "budget": source.get("budget") or {"maxViews": 12},
        "ontologyProfile": source.get("ontologyProfile") or "",
        "baseId": "" if pack_id == DEFAULT_RULE_PACK_ID else pack_id,
    }
    return create_rule_pack(platform, workspace_id, actor, body)


def _require_record(platform: Any, workspace_id: str, pack_id: str) -> dict[str, Any]:
    if pack_id == DEFAULT_RULE_PACK_ID:
        raise ValidationError("builtin rule pack is read-only")
    row = _stored(platform, workspace_id, pack_id)
    if row is None:
        raise NotFoundError(f"rule pack not found: {pack_id!r}")
    return row


def publish_rule_pack(platform: Any, workspace_id: str, actor: str,
                      pack_id: str, note: str = "") -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "owner")
    row = _require_record(platform, workspace_id, pack_id)
    pack, issues = check_rule_pack(row)
    if issues or pack is None:
        raise ValidationError(
            "rule pack validation failed: "
            + "; ".join(issue["message"] for issue in issues[:3]))
    # One published pack per workspace; previous versions are archived so the
    # active version is unambiguous.
    for other in _list_stored(platform, workspace_id):
        if other.get("id") == pack_id:
            continue
        if other.get("status") == "published":
            other = {**other, "status": "archived", "updated_at": _now()}
            platform.ctx.repo.put(
                RULE_PACK_KIND, workspace_id, other["id"], other)
    published = {
        **row,
        **pack.to_dict(),
        "id": pack_id,
        "hash": _pack_hash(pack),
        "status": "published",
        "published_at": _now(),
        "published_by": actor,
        "publish_note": str(note or ""),
        "updated_at": _now(),
    }
    platform.ctx.repo.put(RULE_PACK_KIND, workspace_id, pack_id, published)
    platform.ctx.audit_event(
        actor, workspace_id, "smart_ui.rule_pack.publish", pack_id,
        {"hash": published["hash"], "note": note})
    return _public(published)


def rollback_rule_pack(platform: Any, workspace_id: str, actor: str,
                       pack_id: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "owner")
    row = _require_record(platform, workspace_id, pack_id)
    # Rolling back is a publish of an older record: keep the single-published
    # invariant by archiving any currently active version first.
    for other in _list_stored(platform, workspace_id):
        if other.get("id") == pack_id:
            continue
        if other.get("status") == "published":
            other = {**other, "status": "archived", "updated_at": _now()}
            platform.ctx.repo.put(
                RULE_PACK_KIND, workspace_id, other["id"], other)
    published = {
        **row,
        "status": "published",
        "published_at": _now(),
        "published_by": actor,
        "updated_at": _now(),
    }
    platform.ctx.repo.put(RULE_PACK_KIND, workspace_id, pack_id, published)
    platform.ctx.audit_event(
        actor, workspace_id, "smart_ui.rule_pack.rollback", pack_id, {})
    return _public(published)


def archive_rule_pack(platform: Any, workspace_id: str, actor: str,
                      pack_id: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "owner")
    row = _require_record(platform, workspace_id, pack_id)
    archived = {**row, "status": "archived", "updated_at": _now()}
    platform.ctx.repo.put(RULE_PACK_KIND, workspace_id, pack_id, archived)
    platform.ctx.audit_event(
        actor, workspace_id, "smart_ui.rule_pack.archive", pack_id, {})
    return _public(archived)


def resolve_rule_pack(platform: Any, workspace_id: str, actor: str,
                      requested_id: str = "",
                      workspace_pack: WorkspacePack | None = None) -> dict[str, Any]:
    """Resolve request override -> pack binding -> workspace config -> builtin."""
    wanted = str(requested_id or "").strip()
    if wanted:
        record = get_rule_pack(platform, workspace_id, actor, wanted)
        if record.get("status") == "archived":
            raise ValidationError(f"rule pack {wanted!r} is archived")
        return record
    if workspace_pack is not None and workspace_pack.rule_pack:
        record = get_rule_pack(
            platform, workspace_id, actor, workspace_pack.rule_pack)
        if record.get("status") != "archived":
            return record
    bound = workspace_rule_pack_id(platform, workspace_id, actor)
    if bound:
        record = get_rule_pack(platform, workspace_id, actor, bound)
        if record.get("status") != "archived":
            return record
    return _public(_builtin_record())


def bind_rule_pack(platform: Any, workspace_id: str, actor: str,
                   pack_id: str) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    wanted = str(pack_id or "").strip()
    if wanted and wanted != DEFAULT_RULE_PACK_ID:
        row = _stored(platform, workspace_id, wanted)
        if row is None:
            raise NotFoundError(f"rule pack not found: {wanted!r}")
        if row.get("status") == "archived":
            raise ValidationError(f"rule pack {wanted!r} is archived")
    return set_workspace_rule_pack(platform, workspace_id, actor, wanted)


def dry_run_rule_pack(platform: Any, workspace_id: str, actor: str,
                      pack_id: str, body: dict[str, Any] | None = None
                      ) -> dict[str, Any]:
    platform.ctx.require(actor, workspace_id, "editor")
    body = body or {}
    payload = body.get("pack") if isinstance(body.get("pack"), dict) else None
    if payload is None:
        payload = get_rule_pack(platform, workspace_id, actor, pack_id)
    pack, issues = check_rule_pack(payload)
    if issues or pack is None:
        return {"ok": False, "issues": issues, "hash": ""}
    question = str(body.get("question") or "").strip()
    if not question:
        question = "查看当前对象的状态与关键指标"
    roles = [str(role) for role in (body.get("roles") or ["owner"])]
    focus = body.get("focus") if isinstance(body.get("focus"), dict) else None
    try:
        result = compile_smart_ui(
            platform, workspace_id, actor, question, roles, focus=focus,
            rule_pack=pack.to_dict())
    except PlatformError as exc:
        return {
            "ok": False,
            "issues": [{"code": "E_RULE_DRY_RUN", "ruleId": "",
                        "message": str(exc)}],
            "hash": _pack_hash(pack),
            "pack": pack.to_dict(),
        }
    compiled = result.get("compiled") or {}
    artifacts = compiled.get("artifacts") or {}
    return {
        "ok": True,
        "issues": [],
        "hash": _pack_hash(pack),
        "pack": pack.to_dict(),
        "blueprint": compiled.get("blueprint"),
        "ruleTrace": artifacts.get("ruleTrace") or {},
        "clarify": artifacts.get("clarify") or [],
        "requestedPerspectives": artifacts.get("requestedPerspectives") or [],
        "finalPerspectives": artifacts.get("finalPerspectives") or [],
        "prunedPerspectives": artifacts.get("prunedPerspectives") or [],
    }
