"""Workspace observability metrics (plan-v2 1.9).

A single read-only projection over the platform repositories, the outbox and
the runtime change stream.  It is intentionally cheap: no projection runs, no
reconciliation - just counters the console and ``GET /metrics`` expose.
"""

from __future__ import annotations

from .common import ApplicationContext
from .events import EventLog
from .releases import current_release

#: the documented indicator set (order is the API contract)
INDICATORS = (
    "objects_total",
    "relations_total",
    "schema_version",
    "releases_total",
    "capabilities_total",
    "executions_total",
    "executions_failed",
    "executions_unknown",
    "events_pending",
    "projections_total",
    "changes_total",
    "controller_proposals_total",
    "controller_proposals_rejected",
    "schema_fingerprint_mismatches",
)


class WorkspaceMetrics:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx
        self._events = EventLog(ctx)

    def __call__(self, workspace_id: str, actor: str,
                 window_seconds: int | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return self.collect(workspace_id, window_seconds=window_seconds)

    def collect(self, workspace_id: str,
                window_seconds: int | None = None) -> dict:
        ctx = self._ctx
        from .slo import (DEFAULT_WINDOW_SECONDS, _now, _within,
                         _validate_window)

        window = (DEFAULT_WINDOW_SECONDS if window_seconds is None
                  else _validate_window(window_seconds))
        now = _now(ctx)
        all_executions = ctx.repo.list("execution", workspace_id)
        executions = [
            e for e in all_executions
            if _within(e.get("finished_at") or e.get("created_at")
                       or e.get("started_at"), now, window)
        ]
        current = current_release(ctx, workspace_id)
        from .slo import ServiceLevels

        slo = ServiceLevels(ctx).evaluate(workspace_id,
                                          window_seconds=window)
        counters = {
            "objects_total": _runtime_count(ctx, workspace_id),
            "relations_total": _relation_count(ctx, workspace_id),
            "schema_version": (current or {}).get("schema_version"),
            "releases_total": len(ctx.repo.list("release", workspace_id)),
            "capabilities_total": len(ctx.repo.list("capability", workspace_id)),
            "executions_total": len(executions),
            "executions_succeeded": sum(
                1 for e in executions if e.get("status") == "succeeded"),
            "executions_failed": sum(
                1 for e in executions
                if e.get("status") in ("failed", "refused")),
            "executions_unknown": sum(
                1 for e in executions if e.get("status") == "unknown"),
            "executions_all_time": len(all_executions),
            "events_pending": len(self._events.pending(workspace_id)),
            "events_unrouted": sum(
                1 for e in ctx.repo.list("event", workspace_id)
                if e.get("delivery") == "no_subscribers"),
            "events_dead_lettered": len(self._events.dead_letters(workspace_id)),
            "projections_total": len(ctx.repo.list("projection", workspace_id)),
            "changes_total": _last_change_seq(ctx, workspace_id),
            **_controller_counters(ctx, workspace_id),
        }
        return {
            "workspace_id": workspace_id,
            "generated_at": ctx.clock(),
            "window_seconds": window,
            "window_mode": "windowed",
            "deployment": {
                "release_id": (current or {}).get("id"),
                "schema_version": (current or {}).get("schema_version"),
                "fingerprint": (current or {}).get("fingerprint"),
                "status": ((ctx.repo.get("deployment", workspace_id,
                                        "current") or {}).get("status")),
            },
            "indicators": counters,
            "slo": slo,
            "data_quality": _data_quality(ctx, workspace_id),
            "last_projection": _last_projection(ctx, workspace_id),
        }


def _gateway(ctx: ApplicationContext, workspace_id: str):
    try:
        return ctx.gateway_for(workspace_id)
    except Exception:  # nothing published yet: metrics must stay readable
        return None


def _object_types(ctx: ApplicationContext, workspace_id: str) -> list[str]:
    items = ctx.repo.list("ontology", workspace_id)
    if not items:
        return []
    objects = items[0].get("objects") or {}
    if isinstance(objects, dict):
        return sorted(objects)
    return [o.get("name") for o in objects if isinstance(o, dict)]


def _runtime_count(ctx: ApplicationContext, workspace_id: str) -> int:
    gateway = _gateway(ctx, workspace_id)
    if gateway is None:
        return 0
    total = 0
    for name in _object_types(ctx, workspace_id):
        try:
            total += len(gateway.find_objects(name, report=True)["objects"])
        except Exception:  # type not deployed yet; count what is available
            continue
    return total


def _relation_count(ctx: ApplicationContext, workspace_id: str) -> int:
    """Declared relations in the deployed schema.

    Relation *instances* have no cheap count in the kernel port; the declared
    schema count is the stable indicator (console shows link counts per
    object separately).
    """
    gateway = _gateway(ctx, workspace_id)
    if gateway is None:
        return 0
    try:
        described = gateway.describe()
    except Exception:
        return 0
    relations = described.get("relations") or {}
    return len(relations if isinstance(relations, list) else list(relations))


def _last_change_seq(ctx: ApplicationContext, workspace_id: str) -> int:
    gateway = _gateway(ctx, workspace_id)
    if gateway is None:
        return 0
    try:
        changes = gateway.observe_changes(0)
    except Exception:
        return 0
    return int(changes[-1]["seq"]) if changes else 0


def _controller_counters(ctx: ApplicationContext, workspace_id: str) -> dict:
    """Slow-loop control quality (plan-v2 2.2/2.3).

    Proposal rejection rate tells operators how often the deterministic
    controller wanted an action the policy refused; fingerprint mismatches
    count deployments whose self-healed runtime schema no longer matches the
    released artifact.
    """
    try:
        audits = ctx.audit.list(workspace_id)
    except Exception:
        audits = []
    proposals = 0
    rejected = 0
    mismatches = 0
    for entry in audits:
        action = entry.get("action")
        detail = entry.get("detail") or {}
        if action == "controller.cycle":
            proposals += int(detail.get("proposed") or 0)
            rejected += int(detail.get("rejected") or 0)
        elif action == "schema.fingerprint_mismatch":
            mismatches += 1
    return {
        "controller_proposals_total": proposals,
        "controller_proposals_rejected": rejected,
        "schema_fingerprint_mismatches": mismatches,
    }


def _data_quality(ctx: ApplicationContext, workspace_id: str) -> dict:
    """Explicit data-quality facts instead of an implicit healthy default."""
    runs = ctx.repo.list("projection_run", workspace_id)
    skipped = sum(1 for r in runs if r.get("status") == "skipped")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    open_todos = sum(1 for t in ctx.repo.list("todo", workspace_id)
                     if t.get("status") == "open")
    return {
        "projection_runs": len(runs),
        "projection_skipped": skipped,
        "projection_failed": failed,
        "open_todos": open_todos,
        "last_check_at": (max((r.get("checked_at") or r.get("finished_at")
                               or "" for r in runs), default="") or None),
        "last_change_at": (max((r.get("finished_at") or ""
                                for r in runs
                                if r.get("status") == "succeeded"),
                               default="") or None),
        "unknown_executions": sum(
            1 for e in ctx.repo.list("execution", workspace_id)
            if e.get("status") == "unknown" and not e.get("resolution")),
    }


def _last_projection(ctx: ApplicationContext, workspace_id: str) -> dict | None:
    runs = [d for d in ctx.repo.list("projection_run", workspace_id)]
    if not runs:
        return None
    runs.sort(key=lambda d: d.get("started_at") or "")
    last = runs[-1]
    return {
        "projection_id": last.get("projection_id"),
        "status": last.get("status"),
        "watermark": last.get("watermark"),
        "finished_at": last.get("finished_at"),
        "checked_at": last.get("checked_at") or last.get("finished_at"),
    }
