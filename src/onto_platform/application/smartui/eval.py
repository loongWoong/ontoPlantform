"""P2-F: generation evaluation aggregation (read-only, deterministic).

The Smart-UI studio already records per-generation status, duration, token
usage and optional cost.  This module rolls those records up into a reviewable
scorecard so a team can set a baseline and compare a later generation batch
without changing the generation pipeline.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from ...errors import NotFoundError
from .studio import GENERATION_KIND


def _duration_ms(row: dict) -> int | None:
    try:
        started = datetime.fromisoformat(str(row.get("created_at")))
        finished = datetime.fromisoformat(
            str(row.get("finished_at") or row.get("updated_at")))
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return max(0, int((finished - started).total_seconds() * 1000))


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       math.ceil(fraction * len(ordered)) - 1))
    return int(ordered[index])


def _usage_of(row: dict) -> dict[str, int]:
    trace = row.get("trace") or {}
    metrics = row.get("metrics") or {}
    usage = trace.get("usage") or metrics.get("usage") or {}
    if not isinstance(usage, dict):
        return {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens")
                     or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    return {"promptTokens": prompt, "completionTokens": completion,
            "totalTokens": total}


def _summarize(rows: list[dict]) -> dict[str, Any]:
    count = len(rows)
    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    succeeded = statuses.get("succeeded", 0)
    failed = statuses.get("failed", 0)

    durations = [value for value in (_duration_ms(row) for row in rows)
                 if value is not None]
    prompt = completion = total_tokens = 0
    cost_total = 0.0
    currencies: list[str] = []
    view_total = 0
    program_hashes: set[str] = set()
    for row in rows:
        usage = _usage_of(row)
        prompt += usage["promptTokens"]
        completion += usage["completionTokens"]
        total_tokens += usage["totalTokens"]
        metrics = row.get("metrics") or {}
        cost = metrics.get("cost") if isinstance(metrics, dict) else None
        if isinstance(cost, dict):
            try:
                cost_total += float(cost.get("amount") or 0)
            except (TypeError, ValueError):
                pass
            currency = str(cost.get("currency") or "")
            if currency and currency not in currencies:
                currencies.append(currency)
        try:
            view_total += int(row.get("view_count") or 0)
        except (TypeError, ValueError):
            pass
        trace = row.get("trace") or {}
        program_hash = str(trace.get("programHash") or "")
        if program_hash:
            program_hashes.add(program_hash)

    return {
        "count": count,
        "byStatus": statuses,
        "succeeded": succeeded,
        "failed": failed,
        "successRate": round(succeeded / count, 4) if count else 0.0,
        "duration": {
            "p50Ms": _percentile(durations, 0.50),
            "p95Ms": _percentile(durations, 0.95),
            "avgMs": int(sum(durations) / len(durations)) if durations else 0,
        },
        "usage": {
            "promptTokens": prompt,
            "completionTokens": completion,
            "totalTokens": total_tokens,
        },
        "cost": {
            "total": round(cost_total, 6),
            "currency": currencies[0] if len(currencies) == 1 else "",
        },
        "views": {
            "total": view_total,
            "average": round(view_total / count, 2) if count else 0.0,
        },
        "programHashCount": len(program_hashes),
    }


def generation_eval(platform: Any, workspace_id: str, actor: str,
                    baseline_id: str | None = None) -> dict[str, Any]:
    """Aggregate a workspace's generations and optionally compare a baseline."""
    platform.ctx.require(actor, workspace_id, "viewer")
    rows = [dict(row) for row in
            platform.ctx.repo.list(GENERATION_KIND, workspace_id)]
    current = _summarize(rows)

    baseline: dict[str, Any] | None = None
    delta: dict[str, Any] | None = None
    if baseline_id:
        row = platform.ctx.repo.get(GENERATION_KIND, workspace_id, baseline_id)
        if row is None:
            raise NotFoundError(
                f"generation not found: {baseline_id!r}")
        baseline = {"id": baseline_id, **_summarize([dict(row)])}
        delta = {
            "successRate": round(
                current["successRate"] - baseline["successRate"], 4),
            "p95Ms": current["duration"]["p95Ms"] - baseline["duration"]["p95Ms"],
            "totalCost": round(
                current["cost"]["total"] - baseline["cost"]["total"], 6),
            "avgViewCount": round(
                current["views"]["average"] - baseline["views"]["average"], 2),
        }

    return {
        "workspace_id": workspace_id,
        "baselineId": baseline_id or "",
        "current": current,
        "baseline": baseline,
        "delta": delta,
    }
