"""P2.4: replayable eval sets and the deterministic quality score model.

The tests build a real, succeeded deterministic generation as a template (the
same way ``test_smart_ui_plugin_runtime.py`` does), then clone it into
controlled rows: replay works off the stored blueprint, so the clones keep a
compilable blueprint while the tests vary status/latency/cost/views/hash.

Replay must stay read-only (no generation mutation, no model call), capture
per-case errors instead of aborting, and score every case with the published
weights (success 0.35, replay 0.25, latency 0.15, cost 0.15, views 0.10).
"""

from __future__ import annotations

import copy
import time

import pytest

from onto_platform.application.smartui.eval import generation_eval
from onto_platform.application.smartui.eval_set import (
    EVAL_SET_KIND,
    EVAL_SET_STATUSES,
    MAX_CASES,
    WEIGHTS,
    EvalSetUseCases,
)
from onto_platform.application.smartui.studio import (
    GENERATION_KIND,
    start_generation,
)
from onto_platform.errors import (
    AccessDeniedError,
    NotFoundError,
    ValidationError,
)

QUESTION = "查看车辆情况"
GOOD_CASE = {"id": "c1", "name": "默认", "question": QUESTION}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _svc(platform) -> EvalSetUseCases:
    return EvalSetUseCases(platform.ctx, platform)


def _wait_generation(platform, workspace_id: str, generation_id: str,
                     timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        row = platform.ctx.repo.get(
            GENERATION_KIND, workspace_id, generation_id)
        if row and row.get("status") in {"succeeded", "failed", "cancelled"}:
            return row
        time.sleep(0.02)
    raise AssertionError("deterministic generation did not settle")


@pytest.fixture()
def template(platform, workspace_id):
    started = start_generation(platform, workspace_id, "admin", QUESTION,
                               ["owner"], mode="deterministic")
    row = _wait_generation(platform, workspace_id, started["id"])
    assert row["status"] == "succeeded"
    assert row.get("blueprint")
    return row


def _generation(platform, workspace_id: str, generation_id: str,
                template: dict, *, status: str = "succeeded",
                question: str = QUESTION, seconds: int = 1,
                view_count: int = 0, cost: dict | None = None,
                roles: list[str] | None = None) -> dict:
    """Clone the compiled template into a controlled generation row."""
    row = copy.deepcopy(template)
    row["id"] = generation_id
    row["status"] = status
    row["question"] = question
    if roles is not None:
        row["roles"] = list(roles)
    # The fixture's own template row is newer than these fixed timestamps on
    # purpose: question resolution must not accidentally pick the template.
    created = "2027-01-01T00:00:00+00:00"
    finished = f"2027-01-01T00:00:{seconds:02d}+00:00"
    row["created_at"] = created
    row["updated_at"] = finished
    row["finished_at"] = finished
    row["view_count"] = view_count
    row["metrics"] = {"cost": copy.deepcopy(cost)} if cost else {}
    platform.ctx.repo.put(GENERATION_KIND, workspace_id, generation_id, row)
    return row


def _hash(template: dict) -> str:
    trace = template.get("trace") or {}
    assert trace.get("programHash")
    return str(trace["programHash"])


def _audits(platform, workspace_id: str, action: str) -> list[dict]:
    return [entry for entry in platform.ctx.audit.list(
        workspace_id, limit=2000) if entry.get("action") == action]


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------


def test_create_list_get_and_upsert_by_name(platform, workspace_id):
    platform.workspaces.add_member(workspace_id, "bob", "editor", "admin")
    platform.workspaces.add_member(workspace_id, "carol", "viewer", "admin")
    svc = EvalSetUseCases(platform.ctx)

    created = svc.create(workspace_id, "bob", "质量基线",
                         [GOOD_CASE], description="第一版")
    assert created["id"].startswith("evs_")
    assert created["workspace_id"] == workspace_id
    assert created["status"] == "active"
    assert created["version"] == 1
    assert created["created_by"] == "bob"
    assert created["baseline_id"] == ""
    assert created["cases"][0]["id"] == "c1"
    assert platform.ctx.repo.get(
        EVAL_SET_KIND, workspace_id, created["id"]) is not None

    assert [row["id"] for row in svc.list(workspace_id, "carol")] == [
        created["id"]]
    stored = svc.get(workspace_id, "carol", created["id"])
    assert {key: value for key, value in stored.items()
            if key != "tenant_id"} == {
                key: value for key, value in created.items()
                if key != "tenant_id"}

    updated = svc.create(
        workspace_id, "bob", "质量基线",
        [{"id": "c2", "name": "新用例", "question": "查看收费站"}],
        description="第二版")
    assert updated["id"] == created["id"], "same name upserts, no duplicate row"
    assert updated["version"] == 2
    assert updated["created_at"] == created["created_at"]
    assert [case["id"] for case in updated["cases"]] == ["c2"]
    assert updated["description"] == "第二版"
    assert len(svc.list(workspace_id, "admin")) == 1

    with pytest.raises(AccessDeniedError):
        svc.list(workspace_id, "outsider")
    with pytest.raises(AccessDeniedError):
        svc.create(workspace_id, "carol", "越权创建", [GOOD_CASE])


def test_create_validates_cases_and_metadata(platform, workspace_id):
    svc = EvalSetUseCases(platform.ctx)
    many = [{"id": f"c{index}", "name": "x", "question": QUESTION}
            for index in range(MAX_CASES + 1)]

    with pytest.raises(ValidationError, match="name"):
        svc.create(workspace_id, "admin", "   ", [GOOD_CASE])
    with pytest.raises(ValidationError, match="at most 128"):
        svc.create(workspace_id, "admin", "x" * 129, [GOOD_CASE])
    with pytest.raises(ValidationError, match="at least one case"):
        svc.create(workspace_id, "admin", "空集", [])
    with pytest.raises(ValidationError, match="at most 100"):
        svc.create(workspace_id, "admin", "太多", many)
    with pytest.raises(ValidationError, match="must be a list"):
        svc.create(workspace_id, "admin", "非列表", "not-a-list")
    with pytest.raises(ValidationError, match="must be an object"):
        svc.create(workspace_id, "admin", "坏 case", ["not-a-case"])
    with pytest.raises(ValidationError, match="duplicate case id"):
        svc.create(workspace_id, "admin", "重复", [GOOD_CASE, GOOD_CASE])
    with pytest.raises(ValidationError, match="generation_id or a question"):
        svc.create(workspace_id, "admin", "无目标",
                   [{"id": "c1", "name": "x"}])
    with pytest.raises(ValidationError, match="roles"):
        svc.create(workspace_id, "admin", "坏 roles",
                   [{**GOOD_CASE, "roles": "owner"}])
    with pytest.raises(ValidationError, match="max_duration_ms"):
        svc.create(workspace_id, "admin", "负延迟",
                   [{**GOOD_CASE, "max_duration_ms": -5}])
    with pytest.raises(ValidationError, match="max_cost"):
        svc.create(workspace_id, "admin", "坏成本",
                   [{**GOOD_CASE, "max_cost": "cheap"}])
    with pytest.raises(ValidationError, match="min_view_count"):
        svc.create(workspace_id, "admin", "负视图",
                   [{**GOOD_CASE, "min_view_count": -1}])
    with pytest.raises(ValidationError, match="description"):
        svc.create(workspace_id, "admin", "长描述", [GOOD_CASE],
                   description="x" * 1001)
    with pytest.raises(ValidationError, match="status"):
        svc.create(workspace_id, "admin", "坏状态", [GOOD_CASE],
                   status="archived")
    with pytest.raises(ValidationError, match="baseline"):
        svc.create(workspace_id, "admin", "坏基线", [GOOD_CASE],
                   baseline_id="evs_missing")

    assert EVAL_SET_STATUSES == ("draft", "active")
    assert svc.list(workspace_id, "admin") == [], "invalid input writes nothing"


def test_delete_requires_editor_and_is_audited(platform, workspace_id):
    platform.workspaces.add_member(workspace_id, "carol", "viewer", "admin")
    svc = EvalSetUseCases(platform.ctx)
    created = svc.create(workspace_id, "admin", "待删除", [GOOD_CASE])

    with pytest.raises(AccessDeniedError):
        svc.delete(workspace_id, "carol", created["id"])

    result = svc.delete(workspace_id, "admin", created["id"])
    assert result == {"id": created["id"], "deleted": True, "name": "待删除"}
    with pytest.raises(NotFoundError):
        svc.get(workspace_id, "admin", created["id"])
    with pytest.raises(NotFoundError):
        svc.delete(workspace_id, "admin", created["id"])

    audits = _audits(platform, workspace_id, "smartui.eval_set_deleted")
    assert len(audits) == 1
    assert audits[0]["target"] == created["id"]
    assert audits[0]["detail"]["cases"] == 1


# ----------------------------------------------------------------------
# replay + scoring
# ----------------------------------------------------------------------


def test_replay_all_constraints_pass_scores_100(platform, workspace_id,
                                                template):
    digest = _hash(template)
    _generation(platform, workspace_id, "gen_ok", template, seconds=1,
                view_count=5, cost={"amount": 0.01, "currency": "CNY"})
    svc = _svc(platform)
    created = svc.create(workspace_id, "admin", "全约束", [{
        "id": "c1", "name": "全约束用例", "generation_id": "gen_ok",
        "expected_program_hash": digest, "max_duration_ms": 2000,
        "max_cost": 0.02, "min_view_count": 3,
    }])

    report = svc.replay(workspace_id, "admin", created["id"])

    assert report["workspace_id"] == workspace_id
    assert report["evalSetId"] == created["id"]
    assert report["replayedAt"]
    assert report["baseline"] is None
    assert "scoreDelta" not in report["summary"]

    summary = report["summary"]
    assert summary["total"] == 1
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["errors"] == 0
    assert summary["passRate"] == 1.0
    assert summary["score"] == 100.0
    assert summary["grade"] == "A"

    case = report["cases"][0]
    assert case["id"] == "c1"
    assert case["passed"] is True
    assert case["score"] == 100.0
    assert case["error"] is None
    assert case["targetId"] == "gen_ok"
    assert [check["id"] for check in case["checks"]] == [
        "success", "replay", "programHash", "latency", "cost", "views"]
    assert all(check["passed"] is True for check in case["checks"])
    assert case["replay"]["ok"] is True
    assert case["replay"]["generationId"] == "gen_ok"
    assert case["replay"]["durationMs"] == 1000
    assert case["replay"]["cost"] == 0.01
    assert case["replay"]["viewCount"] == 5

    assert report["scoreModel"]["weights"] == WEIGHTS
    assert report["scoreModel"]["maxScore"] == 100
    assert report["scoreModel"]["grades"]["A"] == ">=90"

    audits = _audits(platform, workspace_id, "smartui.eval_set_replayed")
    assert len(audits) == 1
    assert audits[0]["target"] == created["id"]
    assert audits[0]["detail"]["cases"] == 1
    assert audits[0]["detail"]["score"] == 100.0
    assert audits[0]["detail"]["passRate"] == 1.0


def test_replay_reports_each_failed_check_and_weighted_score(
        platform, workspace_id, template):
    digest = _hash(template)
    _generation(platform, workspace_id, "gen_ok", template, seconds=1,
                view_count=5, cost={"amount": 0.01})
    _generation(platform, workspace_id, "gen_slow", template, seconds=5)
    _generation(platform, workspace_id, "gen_costly", template,
                cost={"amount": 0.5})
    _generation(platform, workspace_id, "gen_fewviews", template,
                view_count=0)
    cases = [
        {"id": "ok", "name": "通过", "generation_id": "gen_ok"},
        {"id": "hash", "name": "哈希不符", "generation_id": "gen_ok",
         "expected_program_hash": "sha256:not-the-real-hash"},
        {"id": "slow", "name": "超时", "generation_id": "gen_slow",
         "max_duration_ms": 1000},
        {"id": "cost", "name": "超成本", "generation_id": "gen_costly",
         "max_cost": 0.1},
        {"id": "views", "name": "视图不足", "generation_id": "gen_fewviews",
         "min_view_count": 3},
    ]
    assert digest
    svc = _svc(platform)
    created = svc.create(workspace_id, "admin", "混合约束", cases)

    report = svc.replay(workspace_id, "admin", created["id"])
    by_id = {case["id"]: case for case in report["cases"]}

    assert by_id["ok"]["passed"] is True
    assert by_id["ok"]["score"] == 100.0

    assert by_id["hash"]["passed"] is False
    assert by_id["hash"]["score"] == 75.0, "replay dimension is 0.25"
    hash_check = {check["id"]: check for check in by_id["hash"]["checks"]}[
        "programHash"]
    assert hash_check["passed"] is False
    assert hash_check["detail"]["observed"] == digest

    assert by_id["slow"]["score"] == 85.0, "latency dimension is 0.15"
    latency = {check["id"]: check for check in by_id["slow"]["checks"]}[
        "latency"]
    assert latency["passed"] is False
    assert latency["detail"] == {"durationMs": 5000, "maxDurationMs": 1000}

    assert by_id["cost"]["score"] == 85.0, "cost dimension is 0.15"
    assert {check["id"]: check for check in by_id["cost"]["checks"]}[
        "cost"]["detail"] == {"amount": 0.5, "maxCost": 0.1}

    assert by_id["views"]["score"] == 90.0, "views dimension is 0.10"
    assert {check["id"]: check for check in by_id["views"]["checks"]}[
        "views"]["detail"] == {"viewCount": 0, "minViewCount": 3}

    summary = report["summary"]
    assert summary["total"] == 5
    assert summary["passed"] == 1
    assert summary["failed"] == 4
    assert summary["errors"] == 0
    assert summary["passRate"] == 0.2
    assert summary["score"] == 87.0, "mean(100, 75, 85, 85, 90)"
    assert summary["grade"] == "B"


def test_replay_question_resolves_latest_succeeded(platform, workspace_id,
                                                   template):
    _generation(platform, workspace_id, "gen_old", template, seconds=1)
    _generation(platform, workspace_id, "gen_new", template, seconds=2)
    _generation(platform, workspace_id, "gen_failed", template, seconds=3,
                status="failed")
    svc = _svc(platform)
    created = svc.create(workspace_id, "admin", "按问题匹配", [
        {"id": "latest", "name": "最新成功", "question": QUESTION},
        {"id": "roles", "name": "角色过滤", "question": QUESTION,
         "roles": ["owner"]},
        {"id": "miss", "name": "没有匹配", "question": "不存在的请求"},
    ])

    report = svc.replay(workspace_id, "admin", created["id"])
    by_id = {case["id"]: case for case in report["cases"]}

    assert by_id["latest"]["targetId"] == "gen_new", (
        "a failed newer generation must not be selected")
    assert by_id["latest"]["passed"] is True
    assert by_id["roles"]["targetId"] == "gen_new"
    assert by_id["roles"]["passed"] is True
    assert by_id["miss"]["passed"] is False
    assert by_id["miss"]["error"] == "no matching generation"
    assert report["summary"]["total"] == 3
    assert report["summary"]["passed"] == 2
    assert report["summary"]["failed"] == 1
    assert report["summary"]["errors"] == 1


def test_replay_captures_missing_and_not_ready_generations(
        platform, workspace_id, template):
    _generation(platform, workspace_id, "gen_ok", template)
    _generation(platform, workspace_id, "gen_broken", template,
                status="failed")
    svc = _svc(platform)
    created = svc.create(workspace_id, "admin", "错误隔离", [
        {"id": "missing", "name": "不存在", "generation_id": "gen_missing"},
        {"id": "ok", "name": "正常", "generation_id": "gen_ok"},
        {"id": "broken", "name": "未就绪", "generation_id": "gen_broken"},
    ])

    report = svc.replay(workspace_id, "admin", created["id"])
    by_id = {case["id"]: case for case in report["cases"]}

    assert "generation not found" in by_id["missing"]["error"]
    assert by_id["missing"]["passed"] is False
    assert by_id["ok"]["passed"] is True
    assert "not ready" in by_id["broken"]["error"]
    assert by_id["broken"]["passed"] is False
    assert report["summary"]["passed"] == 1
    assert report["summary"]["failed"] == 2
    assert report["summary"]["errors"] == 2
    assert report["summary"]["passRate"] == 0.3333


def test_baseline_reports_score_and_pass_rate_deltas(platform, workspace_id,
                                                     template):
    digest = _hash(template)
    _generation(platform, workspace_id, "gen_ok", template, seconds=1,
                view_count=5)
    svc = _svc(platform)

    baseline = svc.create(workspace_id, "admin", "基线集", [{
        "id": "base", "name": "基线", "generation_id": "gen_ok",
        "expected_program_hash": digest,
    }])
    baseline_report = svc.replay(workspace_id, "admin", baseline["id"])
    assert baseline_report["summary"]["score"] == 100.0
    assert baseline_report["summary"]["passRate"] == 1.0

    current = svc.create(workspace_id, "admin", "当前集", [
        {"id": "ok", "name": "通过", "generation_id": "gen_ok",
         "expected_program_hash": digest},
        {"id": "bad", "name": "哈希不符", "generation_id": "gen_ok",
         "expected_program_hash": "sha256:other"},
    ], baseline_id=baseline["id"])
    report = svc.replay(workspace_id, "admin", current["id"])

    assert report["baseline"] == {
        "evalSetId": baseline["id"],
        "score": 100.0,
        "passRate": 1.0,
        "grade": "A",
    }
    assert report["summary"]["score"] == 87.5, "mean(100, 75)"
    assert report["summary"]["passRate"] == 0.5
    assert report["summary"]["scoreDelta"] == -12.5
    assert report["summary"]["passRateDelta"] == -0.5


def test_replay_is_read_only_and_keeps_generation_eval_shape(
        platform, workspace_id, template):
    _generation(platform, workspace_id, "gen_ok", template)
    before = copy.deepcopy(platform.ctx.repo.get(
        GENERATION_KIND, workspace_id, "gen_ok"))
    svc = _svc(platform)
    created = svc.create(workspace_id, "admin", "只读", [{
        "id": "c1", "name": "只读", "generation_id": "gen_ok"}])

    svc.replay(workspace_id, "admin", created["id"])

    after = platform.ctx.repo.get(GENERATION_KIND, workspace_id, "gen_ok")
    assert after == before, "replay must not mutate the generation row"

    scorecard = generation_eval(platform, workspace_id, "admin")
    assert set(scorecard) == {"workspace_id", "baselineId", "current",
                              "baseline", "delta"}
    assert set(scorecard["current"]) == {
        "count", "byStatus", "succeeded", "failed", "successRate",
        "duration", "usage", "cost", "views", "programHashCount"}


def test_replay_needs_the_platform_runtime(platform, workspace_id):
    svc = EvalSetUseCases(platform.ctx)
    created = svc.create(workspace_id, "admin", "缺运行时", [GOOD_CASE])

    with pytest.raises(ValidationError, match="platform runtime"):
        svc.replay(workspace_id, "admin", created["id"])


def test_viewer_can_read_but_cannot_mutate_or_replay_owner_blueprint(
        platform, workspace_id, template):
    platform.workspaces.add_member(workspace_id, "carol", "viewer", "admin")
    _generation(platform, workspace_id, "gen_ok", template)
    svc = _svc(platform)
    created = svc.create(workspace_id, "admin", "只读用户", [{
        "id": "c1", "name": "只读", "generation_id": "gen_ok"}])

    viewer_view = svc.get(workspace_id, "carol", created["id"])
    admin_view = svc.get(workspace_id, "admin", created["id"])
    assert {key: value for key, value in viewer_view.items()
            if key != "tenant_id"} == {
                key: value for key, value in admin_view.items()
                if key != "tenant_id"}
    assert viewer_view["id"] == created["id"]

    # Replay recompiles the stored blueprint *through the calling actor's
    # perspective*, so a blueprint with editor/owner actions cannot be
    # re-rendered for a viewer.  That boundary is explicit, not swallowed.
    with pytest.raises(AccessDeniedError):
        svc.replay(workspace_id, "carol", created["id"])

    with pytest.raises(AccessDeniedError):
        svc.create(workspace_id, "carol", "越权", [GOOD_CASE])
    with pytest.raises(AccessDeniedError):
        svc.delete(workspace_id, "carol", created["id"])
