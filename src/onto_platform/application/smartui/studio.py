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
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ...errors import NotFoundError, ValidationError
from .adjudication import (
    AdjudicationContext,
    AdjudicatorChain,
    DefaultProposalAdjudicator,
    normalize_view_options,
)
from .bridge import (
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
from .catalog import MAX_QUESTION
from .context import ContextBuildRequest, DefaultContextPack
from .contracts import (
    PERMISSION_ARTIFACT_PUBLISH,
    PERMISSION_CONTEXT_READ,
    PERMISSION_PROPOSAL_ADJUDICATE,
    PERMISSION_PROPOSAL_GENERATE,
)
from .generators import (
    GenerationRequest,
    InvalidProposal,
    ModelUnavailable,
    PromptTooLarge,
)
from .packs import apply_view_presets, resolve_generation_pack
from .prompt import DefaultPromptPack, PromptRequest
from .publisher import DefaultPublisher, PublishRequest

MAX_PROMPT_BYTES = 16000
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
        "pluginLock": row.get("plugin_lock") or trace.get("pluginLock"),
        "packId": row.get("pack_id"),
        "packName": (row.get("pack") or {}).get("name"),
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
        "pack": row.get("pack"),
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
                  snapshot: dict[str, Any] | None = None,
                  workspace_pack: Any = None) -> dict[str, Any]:
    """Delegate the prompt-safe workspace projection to the context plugin.

    The built-in implementation is the fallback for callers that build a
    platform without a Smart-UI host (a few unit tests do).  If a host exists,
    the first declared context contribution is authoritative; an implementation
    error is not silently downgraded because the pack is part of the contract.
    """
    host = getattr(platform, "smartui", None) if platform is not None else None
    if host is not None:
        try:
            records = host.find("context", permission=PERMISSION_CONTEXT_READ)
        except Exception:
            records = []
        for record in records:
            implementation = getattr(record, "implementation", None)
            if implementation is not None and hasattr(implementation, "build"):
                return implementation.build(ContextBuildRequest(
                    platform=platform,
                    workspace_id=workspace_id,
                    actor=actor,
                    snapshot=snapshot,
                    workspace_pack=workspace_pack,
                ))
    return DefaultContextPack().build(ContextBuildRequest(
        platform=platform,
        workspace_id=workspace_id,
        actor=actor,
        snapshot=snapshot,
        workspace_pack=workspace_pack,
    ))


def _normalize_view_options(raw: Any, obj: Any, perspective: str,
                            pruned: list[dict[str, Any]],
                            platform: Any | None = None) -> dict[str, Any]:
    """Studio entry point; the shared chain owns plugin selection."""
    return normalize_view_options(raw, obj, perspective, pruned, platform)


def _normalize_proposal(proposal: dict[str, Any],
                        pack: dict[str, Any],
                        question: str,
                        ontology: Any,
                        ontology_version: str,
                        requested_focus: dict[str, Any] | None = None,
                        platform: Any | None = None,
                        ) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Run the ordered adjudicator plugin chain over a loose proposal."""
    context = AdjudicationContext(
        pack=pack,
        question=question,
        ontology=ontology,
        ontology_version=ontology_version,
        requested_focus=requested_focus,
        platform=platform,
    )
    host = getattr(platform, "smartui", None) if platform is not None else None
    adjudicators: list[Any] = []
    if host is not None:
        try:
            adjudicators = [
                record.implementation for record in host.find(
                    "adjudicator", permission=PERMISSION_PROPOSAL_ADJUDICATE)
                if getattr(record, "implementation", None) is not None
            ]
        except Exception:
            adjudicators = []
    if not adjudicators:
        adjudicators = [DefaultProposalAdjudicator()]
    result = AdjudicatorChain(adjudicators).run(proposal, context)
    return result.blueprint, result.checks, result.pruned


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
                     focus: dict[str, Any] | None = None,
                     pack_id: str = "") -> dict[str, Any]:
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
        if not pack_id:
            pack_id = str(parent.get("pack_id") or "")

    workspace_pack = resolve_generation_pack(
        platform, workspace_id, actor, pack_id)
    pack_id = workspace_pack.id if workspace_pack is not None else ""

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
        "pack_id": pack_id,
        "pack": workspace_pack.as_dict() if workspace_pack is not None else None,
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
        "parent_id": parent_id, "mode": mode, "pack_id": pack_id,
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
    host = getattr(platform, "smartui", None)
    workspace_pack = None
    if host is not None and row.get("pack_id"):
        try:
            workspace_pack = host.pack(str(row["pack_id"]))
        except Exception:
            workspace_pack = None
    generator = None
    if host is not None:
        try:
            generator = host.implementation(
                "onto.smartui.generator.deterministic",
                permission=PERMISSION_PROPOSAL_GENERATE)
        except Exception:
            generator = None
    if generator is not None:
        candidate = generator.generate(GenerationRequest(
            platform=platform,
            workspace_id=workspace_id,
            actor=actor,
            question=question,
            roles=roles,
            focus=row.get("focus"),
            mode="deterministic",
        ))
        compiled = candidate.compiled or {}
        blueprint = candidate.blueprint
        generator_id = candidate.source
    else:
        result = compile_smart_ui(
            platform, workspace_id, actor, question, roles, focus=row.get("focus"))
        compiled = result["compiled"]
        blueprint = compiled.get("blueprint")
        generator_id = "onto.smartui.generator.deterministic"
    if not blueprint:
        raise ValidationError("deterministic fallback produced no blueprint")
    blueprint = apply_view_presets(blueprint, workspace_pack)
    if workspace_pack is not None and workspace_pack.view_presets:
        # Presets change the blueprint, so the program must be compiled from
        # the *preset-applied* blueprint rather than the planner's default.
        snapshot = smart_ui_snapshot(
            platform, workspace_id, actor, focus=row.get("focus"))
        compiled = compile_smart_ui_blueprint(
            platform, workspace_id, actor, question, roles, blueprint,
            snapshot=snapshot)["compiled"]
    row["mode"] = "deterministic" if row.get("requested_mode") == "deterministic" else "fallback"
    row["status"] = "succeeded"
    row["blueprint"] = blueprint
    row["program"] = compiled.get("program")
    artifact_meta = dict(compiled.get("artifacts") or {},
                         pluginLock=row.get("plugin_lock") or [])
    if host is not None:
        artifact_meta["engineDescriptors"] = host.engine_descriptor_dicts()
        artifact_meta["renderers"] = host.renderer_dicts()
    row["artifacts"] = artifact_meta
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
        "generatorPlugin": generator_id,
        "pluginLock": row.get("plugin_lock") or [],
        "workspacePack": row.get("pack"),
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
        host = getattr(platform, "smartui", None)
        row["plugin_lock"] = (host.execution_lock_dicts()
                              if host is not None else [])
        row["status"] = "running"
        _mark(row, "context", "running")
        _save(platform, row)

        snapshot = smart_ui_snapshot(
            platform, workspace_id, actor, focus=row.get("focus"))
        _ensure_live(platform, workspace_id, generation_id)
        workspace_pack = None
        if row.get("pack_id"):
            workspace_pack = host.pack(str(row["pack_id"]))
            row["pack"] = workspace_pack.as_dict()
        pack = _context_pack(platform, workspace_id, actor,
                             snapshot=snapshot, workspace_pack=workspace_pack)
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
            "workspacePack": pack.get("workspacePack"),
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

        host = getattr(platform, "smartui", None)
        if host is None:
            raise ValidationError("Smart-UI plugin host is not configured")
        generator = host.implementation(
            "onto.smartui.generator.llm",
            permission=PERMISSION_PROPOSAL_GENERATE)
        try:
            candidate = generator.generate(GenerationRequest(
                platform=platform,
                workspace_id=workspace_id,
                actor=actor,
                question=question,
                roles=roles,
                focus=row.get("focus"),
                base_blueprint=base_blueprint,
                instruction=instruction,
                mode=mode,
                pack=pack,
                snapshot=snapshot,
                provider=provider,
                max_prompt_bytes=MAX_PROMPT_BYTES,
            ))
        except PromptTooLarge as exc:
            _ensure_live(platform, workspace_id, generation_id)
            _fallback(
                platform, workspace_id, actor, row, roles, question,
                f"上下文过大（{exc.prompt_bytes} > {MAX_PROMPT_BYTES} 字节）",
                started)
            return
        except ModelUnavailable as exc:
            _ensure_live(platform, workspace_id, generation_id)
            reason = str(exc) or type(exc).__name__
            if mode == "llm":
                raise ValidationError(
                    f"模型调用失败，且当前为显式模型模式：{reason}") from exc
            _fallback(platform, workspace_id, actor, row, roles, question,
                      f"模型不可用（{reason}）", started)
            return
        except InvalidProposal as exc:
            row["trace"] = {**exc.trace,
                            **({"rawPreview": exc.raw_preview}
                               if exc.raw_preview else {})}
            _save(platform, row)
            raise ValidationError(str(exc)) from exc

        prompt_bytes = int(candidate.trace.get("promptBytes") or 0)
        row["trace"] = dict(candidate.trace)
        row["budget"]["promptBytes"] = prompt_bytes
        model_name = str(candidate.trace.get("model") or "")
        proposal = candidate.proposal
        if proposal is None:
            raise ValidationError("模型未返回可用提议")
        _mark(row, "propose", "running")
        _save(platform, row)


        _ensure_live(platform, workspace_id, generation_id)
        row["proposal"] = proposal
        _mark(row, "propose", "ok", f"{model_name} · {row['trace'].get('responseBytes', 0)} 字节", started)
        _mark(row, "adjudicate", "running")
        _save(platform, row)

        ontology = snapshot["ontology"]
        blueprint, checks, pruned = _normalize_proposal(
            proposal, pack, question, ontology, ontology.ontology_version,
            requested_focus=row.get("focus"), platform=platform)
        blueprint = apply_view_presets(blueprint, workspace_pack)
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
            "pluginLock": row.get("plugin_lock") or [],
            "workspacePack": row.get("pack"),
        })
        row["program"] = compiled.get("program")
        artifact_meta = dict(compiled.get("artifacts") or {},
                             pluginLock=row.get("plugin_lock") or [])
        if host is not None:
            artifact_meta["engineDescriptors"] = host.engine_descriptor_dicts()
            artifact_meta["renderers"] = host.renderer_dicts()
        row["artifacts"] = artifact_meta
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


def replay_generation(platform, workspace_id: str, actor: str,
                      generation_id: str) -> dict[str, Any]:
    """Re-render a succeeded generation and report plugin-lock drift.

    Replay never re-runs the model and never trusts the old program: it
    recompiles the stored adjudicated blueprint against the current snapshot.
    The pinned lock is compared with the current *execution* lock so a changed
    plugin version is visible instead of silently producing a different UI.
    """
    platform.ctx.require(actor, workspace_id, "viewer")
    row = _get(platform, workspace_id, generation_id)
    if row.get("status") != "succeeded" or not row.get("blueprint"):
        raise ValidationError("generation is not ready yet")
    host = getattr(platform, "smartui", None)
    pinned = list(row.get("plugin_lock")
                  or (row.get("trace") or {}).get("pluginLock") or [])
    current = host.execution_lock_dicts() if host is not None else []
    pinned_by_id = {
        str(item.get("id")): (str(item.get("version") or ""),
                             str(item.get("apiVersion") or ""))
        for item in pinned if isinstance(item, dict)
    }
    current_by_id = {
        str(item.get("id")): (str(item.get("version") or ""),
                              str(item.get("apiVersion") or ""))
        for item in current if isinstance(item, dict)
    }
    missing = sorted(set(pinned_by_id) - set(current_by_id))
    added = sorted(set(current_by_id) - set(pinned_by_id))
    changed = sorted(
        plugin_id for plugin_id in set(pinned_by_id) & set(current_by_id)
        if pinned_by_id[plugin_id] != current_by_id[plugin_id])
    result = generation_view(platform, workspace_id, actor, generation_id)
    return {
        **result,
        "replay": {
            "ok": not missing and not changed,
            "pinned": pinned,
            "current": current,
            "missing": missing,
            "changed": changed,
            "added": added,
            "question": row.get("question"),
            "packId": row.get("pack_id"),
        },
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
            "plugin_lock": row.get("plugin_lock") or trace.get("pluginLock") or [],
            "pack": row.get("pack") or trace.get("workspacePack"),
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
        draft["plugin_lock"] = row.get("plugin_lock") or trace.get("pluginLock") or []
        draft["pack"] = row.get("pack") or trace.get("workspacePack")
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
    published_at = _now()
    host = getattr(platform, "smartui", None)
    if host is not None:
        publisher = host.implementation(
            "onto.smartui.publisher.default",
            permission=PERMISSION_ARTIFACT_PUBLISH)
        artifact = publisher.build_artifact(PublishRequest(
            platform=platform,
            workspace_id=workspace_id,
            actor=actor,
            draft=draft,
            source_row=source_row,
            published_at=published_at,
        ))
    else:
        artifact = DefaultPublisher().build_artifact(PublishRequest(
            platform=platform,
            workspace_id=workspace_id,
            actor=actor,
            draft=draft,
            source_row=source_row,
            published_at=published_at,
        ))
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
