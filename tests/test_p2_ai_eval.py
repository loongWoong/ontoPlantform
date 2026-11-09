"""P2-F: AI generation scorecard aggregation and baseline delta."""

from __future__ import annotations

import pytest

from onto_platform.application.smartui.eval import generation_eval
from onto_platform.application.smartui.studio import GENERATION_KIND
from onto_platform.errors import AccessDeniedError, NotFoundError


def _row(platform, wid, gen_id, *, status="succeeded", seconds=1,
         view_count=0, program_hash="", prompt=100, completion=50,
         cost=None) -> dict:
    created = "2026-11-09T00:00:00+00:00"
    finished = f"2026-11-09T00:00:{seconds:02d}+00:00"
    row = {
        "id": gen_id,
        "workspace_id": wid,
        "status": status,
        "created_at": created,
        "updated_at": finished,
        "finished_at": finished,
        "view_count": view_count,
        "trace": {"programHash": program_hash,
                  "usage": {"prompt_tokens": prompt,
                            "completion_tokens": completion,
                            "total_tokens": prompt + completion}},
        "metrics": {"cost": cost} if cost else {},
    }
    platform.ctx.repo.put(GENERATION_KIND, wid, gen_id, row)
    return row


def test_empty_workspace_returns_zeroed_scorecard(platform, workspace_id):
    result = generation_eval(platform, workspace_id, "admin")

    current = result["current"]
    assert current["count"] == 0
    assert current["successRate"] == 0.0
    assert current["duration"] == {"p50Ms": 0, "p95Ms": 0, "avgMs": 0}
    assert current["usage"]["totalTokens"] == 0
    assert current["cost"]["total"] == 0
    assert current["views"]["average"] == 0.0
    assert result["baseline"] is None
    assert result["delta"] is None


def test_scorecard_aggregates_status_latency_usage_and_views(platform, workspace_id):
    _row(platform, workspace_id, "g1", seconds=1, view_count=2,
         program_hash="h1", cost={"amount": 0.01, "currency": "CNY"})
    _row(platform, workspace_id, "g2", seconds=2, view_count=8,
         program_hash="h2", cost={"amount": 0.02, "currency": "CNY"})
    _row(platform, workspace_id, "g3", status="failed", seconds=3,
         program_hash="h2")

    current = generation_eval(platform, workspace_id, "admin")["current"]

    assert current["count"] == 3
    assert current["byStatus"] == {"succeeded": 2, "failed": 1}
    assert current["successRate"] == 0.6667
    assert current["duration"] == {"p50Ms": 2000, "p95Ms": 3000,
                                   "avgMs": 2000}
    assert current["usage"] == {"promptTokens": 300, "completionTokens": 150,
                                "totalTokens": 450}
    assert current["cost"] == {"total": 0.03, "currency": "CNY"}
    assert current["views"] == {"total": 10, "average": 3.33}
    assert current["programHashCount"] == 2


def test_baseline_delta_is_reported(platform, workspace_id):
    baseline = _row(platform, workspace_id, "base", seconds=1, view_count=1,
                    cost={"amount": 0.01, "currency": "CNY"})
    _row(platform, workspace_id, "new", seconds=4, view_count=5,
         cost={"amount": 0.03, "currency": "CNY"})

    result = generation_eval(platform, workspace_id, "admin",
                             baseline_id=baseline["id"])

    assert result["baseline"]["id"] == "base"
    assert result["baseline"]["count"] == 1
    assert result["delta"]["p95Ms"] == 3000
    assert result["delta"]["totalCost"] == 0.03
    assert result["delta"]["avgViewCount"] == 2.0


def test_missing_baseline_is_not_silently_ignored(platform, workspace_id):
    with pytest.raises(NotFoundError):
        generation_eval(platform, workspace_id, "admin",
                        baseline_id="gen_missing")


def test_eval_requires_viewer_access(platform, workspace_id):
    with pytest.raises(AccessDeniedError):
        generation_eval(platform, workspace_id, "outsider")


def test_eval_is_read_only(platform, workspace_id):
    _row(platform, workspace_id, "g1")
    before = platform.ctx.repo.get(GENERATION_KIND, workspace_id, "g1")

    generation_eval(platform, workspace_id, "admin")

    after = platform.ctx.repo.get(GENERATION_KIND, workspace_id, "g1")
    assert after == before
