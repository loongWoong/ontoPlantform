"""P2.4: replayable AI evaluation sets and a deterministic quality score model.

``generation_eval`` (P2-F) aggregates whatever generations happen to exist in a
workspace.  That answers "what did the model do recently" but not "is this
version good enough": there is no curated set to replay, no per-case checks and
no repeatable score to compare against a baseline.

This module adds exactly that, without touching the generation pipeline:

* ``generation_eval_set`` documents: named, editable case lists.  A case names a
  ``generation_id`` (or a ``question`` resolved to the newest succeeded
  generation) plus optional quality constraints (program hash, latency budget,
  cost budget, minimum view count).
* ``EvalSetUseCases.replay`` re-renders every case through
  :func:`onto_platform.application.smartui.studio.replay_generation` - which
  recompiles the stored blueprint against the live snapshot and never calls the
  model - then checks the constraints and scores each case with an explicit,
  deterministic weight model.

The replay is read-only: it creates no generation and rewrites no generation
row (the only write is the required audit entry).  Delivery is explainable by
design: every case carries the individual checks and the weights are published
in the result's ``scoreModel``.

Same-name ``create`` behaves as an upsert inside one workspace: the existing
row is updated, ``version`` incremented and the id kept, so re-registering a
curated set after editing it in source control never accumulates duplicates.
A ``baseline_id`` points at another eval set of the same workspace; replaying
the current set also replays the baseline (without following *its* baseline, so
chains cannot loop) and reports ``scoreDelta`` / ``passRateDelta``.

Scoring model (0-100 per case): each dimension contributes its weight when it
passes and 0 when it fails; a dimension with no matching constraint is skipped
and receives full credit (visible as ``passed: null`` in ``checks``).

    success 0.35   generation status is ``succeeded``
    replay  0.25   replay reported ok and the expected program hash matched
    latency 0.15   recorded duration <= max_duration_ms
    cost    0.15   recorded cost amount <= max_cost
    views   0.10   recorded view_count >= min_view_count

Overall score is the mean case score rounded to 2 decimals; grade is
A >= 90, B >= 80, C >= 70, D >= 60, F otherwise.
"""

from __future__ import annotations

import copy
from typing import Any

from ...errors import NotFoundError, ValidationError
from .eval import _duration_ms
from .studio import GENERATION_KIND, replay_generation

EVAL_SET_KIND = "generation_eval_set"
MAX_CASES = 100
MAX_NAME = 128
MAX_DESCRIPTION = 1000
MAX_CASE_TEXT = 200
EVAL_SET_STATUSES = ("draft", "active")

#: Published, frozen weights of the quality score (must sum to 1.0).
WEIGHTS: dict[str, float] = {
    "success": 0.35,
    "replay": 0.25,
    "latency": 0.15,
    "cost": 0.15,
    "views": 0.10,
}

_CHECKS = ("success", "replay", "programHash", "latency", "cost", "views")

_SCORE_MODEL: dict[str, Any] = {
    "weights": dict(WEIGHTS),
    "maxScore": 100,
    "formula": (
        "caseScore = 100 * sum(weight for each passing or unconstrained "
        "dimension); overallScore = mean(caseScore)"),
    "skipped": "a dimension without a constraint is skipped and gets full credit",
    "grades": {"A": ">=90", "B": ">=80", "C": ">=70", "D": ">=60", "F": "<60"},
}


def _text(value: object, label: str, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    text = value.strip()
    if limit is not None and len(text) > limit:
        raise ValidationError(f"{label} must be at most {limit} characters")
    return text


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{label} must be an integer")
    if value < 0:
        raise ValidationError(f"{label} must not be negative")
    return value


def _optional_cost(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a number")
    number = float(value)
    if number < 0:
        raise ValidationError(f"{label} must not be negative")
    return number


def _string_list(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValidationError(f"{label} must be a list of strings")
    out: list[str] = []
    for item in value:
        out.append(_text(item, f"{label} entry", MAX_CASE_TEXT))
    return out


def _normalize_case(raw: object, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError(f"case[{index}] must be an object")
    case_id = _text(raw.get("id"), f"case[{index}].id", MAX_CASE_TEXT)
    name = _text(raw.get("name"), f"case {case_id!r}.name", MAX_CASE_TEXT)
    generation_id = raw.get("generation_id")
    question = raw.get("question")
    generation_id = ("" if generation_id is None
                     else _text(generation_id, f"case {case_id!r}.generation_id",
                                MAX_CASE_TEXT))
    question = ("" if question is None
                else _text(question, f"case {case_id!r}.question",
                           MAX_CASE_TEXT))
    if not generation_id and not question:
        raise ValidationError(
            f"case {case_id!r} needs a generation_id or a question")
    expected = raw.get("expected_program_hash")
    expected = ("" if expected is None else _text(
        expected, f"case {case_id!r}.expected_program_hash", MAX_CASE_TEXT))
    return {
        "id": case_id,
        "name": name,
        "generation_id": generation_id,
        "question": question,
        "roles": _string_list(raw.get("roles"), f"case {case_id!r}.roles"),
        "tags": _string_list(raw.get("tags"), f"case {case_id!r}.tags"),
        "max_duration_ms": _optional_int(
            raw.get("max_duration_ms"), f"case {case_id!r}.max_duration_ms"),
        "max_cost": _optional_cost(
            raw.get("max_cost"), f"case {case_id!r}.max_cost"),
        "min_view_count": _optional_int(
            raw.get("min_view_count"), f"case {case_id!r}.min_view_count"),
        "expected_program_hash": expected,
    }


def _normalize_cases(cases: object) -> list[dict[str, Any]]:
    if isinstance(cases, (str, bytes)) or not isinstance(cases, (list, tuple)):
        raise ValidationError("cases must be a list")
    if not cases:
        raise ValidationError("cases must contain at least one case")
    if len(cases) > MAX_CASES:
        raise ValidationError(f"cases must contain at most {MAX_CASES} cases")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(cases):
        case = _normalize_case(raw, index)
        if case["id"] in seen:
            raise ValidationError(f"duplicate case id: {case['id']!r}")
        seen.add(case["id"])
        normalized.append(case)
    return normalized


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _cost_amount(row: dict) -> float | None:
    metrics = row.get("metrics") or {}
    cost = metrics.get("cost") if isinstance(metrics, dict) else None
    if not isinstance(cost, dict):
        return None
    try:
        return float(cost.get("amount"))
    except (TypeError, ValueError):
        return None


class EvalSetUseCases:
    """Create/list/get/delete eval sets and replay them into a scored report.

    ``EvalSetUseCases(ctx)`` is enough for the CRUD surface.  Replay needs the
    platform object (it calls ``studio.replay_generation(platform, ...)``), so
    the composition root passes it to the constructor (``EvalSetUseCases(ctx,
    platform)``) or per call (``replay(..., platform=platform)``); without it
    ``replay`` raises ``ValidationError`` instead of guessing.
    """

    def __init__(self, ctx: Any, platform: Any = None) -> None:
        self._ctx = ctx
        self._platform = platform

    def bind_platform(self, platform: Any) -> "EvalSetUseCases":
        """Attach the platform runtime used by :meth:`replay`."""
        self._platform = platform
        return self

    # ------------------------------------------------------------------
    # storage helpers
    # ------------------------------------------------------------------

    def _row(self, workspace_id: str, set_id: str) -> dict:
        row = self._ctx.repo.get(EVAL_SET_KIND, workspace_id, set_id)
        if row is None:
            raise NotFoundError(f"eval set not found: {set_id!r}")
        return copy.deepcopy(row)

    def _by_name(self, workspace_id: str, name: str) -> dict | None:
        for row in self._ctx.repo.list(EVAL_SET_KIND, workspace_id):
            if str(row.get("name") or "") == name:
                return copy.deepcopy(row)
        return None

    def _validate_baseline(self, workspace_id: str, baseline_id: str,
                           own_id: str = "") -> str:
        baseline_id = "" if baseline_id is None else _text(
            baseline_id, "baseline_id", MAX_CASE_TEXT)
        if not baseline_id:
            return ""
        if own_id and baseline_id == own_id:
            raise ValidationError("baseline_id must reference another eval set")
        if self._ctx.repo.get(EVAL_SET_KIND, workspace_id, baseline_id) is None:
            raise ValidationError(
                f"baseline eval set not found: {baseline_id!r}")
        return baseline_id

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, workspace_id: str, actor: str, name: str, cases: list,
               description: str = "", baseline_id: str | None = None,
               *, status: str | None = None) -> dict:
        """Create, or update by name (upsert), one workspace-local eval set.

        Reusing a name updates the existing row, bumps ``version`` and keeps
        the id; ``status`` keeps the stored value unless explicitly passed.
        """
        self._ctx.require(actor, workspace_id, "editor")
        name = _text(name, "name", MAX_NAME)
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValidationError("description must be a string")
        description = description.strip()
        if len(description) > MAX_DESCRIPTION:
            raise ValidationError(
                f"description must be at most {MAX_DESCRIPTION} characters")
        normalized = _normalize_cases(cases)
        if status is not None and status not in EVAL_SET_STATUSES:
            raise ValidationError(
                f"status must be one of {list(EVAL_SET_STATUSES)}")
        now = self._ctx.clock()
        existing = self._by_name(workspace_id, name)
        if existing is not None:
            own_id = str(existing["id"])
            baseline = self._validate_baseline(
                workspace_id, baseline_id, own_id)
            row = existing
            row.update({
                "description": description,
                "cases": normalized,
                "baseline_id": baseline,
                "updated_at": now,
                "version": int(row.get("version") or 1) + 1,
            })
            if status is not None:
                row["status"] = status
            self._ctx.repo.put(EVAL_SET_KIND, workspace_id, own_id, row)
            return self._row(workspace_id, own_id)
        baseline = self._validate_baseline(workspace_id, baseline_id)
        row = {
            "id": f"evs_{self._ctx.id_factory()}",
            "workspace_id": workspace_id,
            "name": name,
            "description": description,
            "status": status or "active",
            "baseline_id": baseline,
            "cases": normalized,
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        self._ctx.repo.put(EVAL_SET_KIND, workspace_id, row["id"], row)
        return self._row(workspace_id, row["id"])

    def list(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        rows = [copy.deepcopy(row)
                for row in self._ctx.repo.list(EVAL_SET_KIND, workspace_id)]
        return sorted(rows, key=lambda row: (str(row.get("name") or ""),
                                             str(row.get("id") or "")))

    def get(self, workspace_id: str, actor: str, set_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._row(workspace_id, set_id)

    def delete(self, workspace_id: str, actor: str, set_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        row = self._row(workspace_id, set_id)
        self._ctx.repo.delete(EVAL_SET_KIND, workspace_id, set_id)
        self._ctx.audit_event(actor, workspace_id,
                              "smartui.eval_set_deleted", set_id,
                              {"name": row.get("name"),
                               "cases": len(row.get("cases") or [])})
        return {"id": set_id, "deleted": True,
                "name": row.get("name", "")}

    # ------------------------------------------------------------------
    # replay + scoring
    # ------------------------------------------------------------------

    def replay(self, workspace_id: str, actor: str, set_id: str,
               *, platform: Any = None) -> dict:
        """Replay every case of one set and score the result.

        Read-only: no generation is created or modified; the only write is the
        ``smartui.eval_set_replayed`` audit entry.  A single case failing
        (missing generation, not-ready generation, failed check) never aborts
        the run - it is captured as that case's ``error``/``passed`` state.
        ``summary.failed`` counts every non-passing case (errors included);
        ``summary.errors`` is the subset whose replay raised.
        """
        self._ctx.require(actor, workspace_id, "viewer")
        runtime = platform or self._platform
        if runtime is None:
            raise ValidationError(
                "eval replay needs the platform runtime; "
                "pass platform=... or construct EvalSetUseCases(ctx, platform)")
        row = self._row(workspace_id, set_id)
        report = self._replay_row(runtime, workspace_id, actor, row)
        baseline = None
        if row.get("baseline_id"):
            baseline_row = self._row(workspace_id, str(row["baseline_id"]))
            baseline_report = self._replay_row(
                runtime, workspace_id, actor, baseline_row)
            baseline = {
                "evalSetId": baseline_report["evalSetId"],
                "score": baseline_report["summary"]["score"],
                "passRate": baseline_report["summary"]["passRate"],
                "grade": baseline_report["summary"]["grade"],
            }
            report["summary"]["scoreDelta"] = round(
                report["summary"]["score"] - baseline["score"], 2)
            report["summary"]["passRateDelta"] = round(
                report["summary"]["passRate"] - baseline["passRate"], 4)
        report["baseline"] = baseline
        self._ctx.audit_event(
            actor, workspace_id, "smartui.eval_set_replayed", set_id,
            {"cases": report["summary"]["total"],
             "passRate": report["summary"]["passRate"],
             "score": report["summary"]["score"],
             "baselineId": str(row.get("baseline_id") or "")})
        return report

    def _replay_row(self, runtime: Any, workspace_id: str, actor: str,
                    row: dict) -> dict:
        cases = [self._evaluate_case(runtime, workspace_id, actor, case)
                 for case in row.get("cases") or []]
        total = len(cases)
        passed = sum(1 for case in cases if case["passed"])
        errors = sum(1 for case in cases if case["error"])
        score = round(sum(case["score"] for case in cases) / total, 2) if total else 0.0
        pass_rate = round(passed / total, 4) if total else 0.0
        summary = {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "errors": errors,
            "passRate": pass_rate,
            "score": score,
            "grade": _grade(score),
        }
        return {
            "workspace_id": workspace_id,
            "evalSetId": row.get("id"),
            "replayedAt": self._ctx.clock(),
            "summary": summary,
            "cases": cases,
            "scoreModel": copy.deepcopy(_SCORE_MODEL),
            "baseline": None,
        }

    def _resolve_target(self, workspace_id: str, case: dict) -> dict:
        generation_id = case.get("generation_id") or ""
        if generation_id:
            row = self._ctx.repo.get(GENERATION_KIND, workspace_id,
                                     generation_id)
            if row is None:
                raise NotFoundError(
                    f"generation not found: {generation_id!r}")
            return copy.deepcopy(row)
        question = str(case.get("question") or "").strip()
        wanted_roles = set(case.get("roles") or [])
        candidates: list[dict] = []
        for raw in self._ctx.repo.list(GENERATION_KIND, workspace_id):
            if str(raw.get("status")) != "succeeded":
                continue
            if str(raw.get("question") or "").strip() != question:
                continue
            if wanted_roles and not wanted_roles <= set(raw.get("roles") or []):
                continue
            candidates.append(copy.deepcopy(raw))
        if not candidates:
            raise NotFoundError("no matching generation")
        return max(candidates, key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("updated_at") or ""),
            str(row.get("id") or "")))

    def _evaluate_case(self, runtime: Any, workspace_id: str, actor: str,
                       case: dict) -> dict:
        result = {
            "id": case.get("id"),
            "name": case.get("name"),
            "passed": False,
            "score": 0.0,
            "checks": [],
            "replay": None,
            "error": None,
            "targetId": case.get("generation_id") or "",
        }
        target: dict | None = None
        try:
            target = self._resolve_target(workspace_id, case)
            replay = replay_generation(runtime, workspace_id, actor,
                                       str(target["id"]))
        except (NotFoundError, ValidationError) as exc:
            return self._error_case(result, case, target, str(exc))
        result["targetId"] = str(target.get("id") or result["targetId"])
        compiled = replay.get("compiled") or {}
        generation = replay.get("generation") or {}
        replay_block = replay.get("replay") or {}
        program_hash = str(
            compiled.get("programHash")
            or generation.get("program_hash")
            or (target.get("trace") or {}).get("programHash")
            or "")
        expected = case.get("expected_program_hash") or ""
        hash_passed: bool | None = (
            None if not expected else program_hash == expected)
        duration = _duration_ms(target)
        max_duration = case.get("max_duration_ms")
        latency_passed: bool | None = (
            None if max_duration is None
            else duration is not None and duration <= max_duration)
        amount = _cost_amount(target)
        max_cost = case.get("max_cost")
        cost_passed: bool | None = (
            None if max_cost is None
            else amount is not None and amount <= max_cost)
        views = int(target.get("view_count") or 0)
        min_views = case.get("min_view_count")
        views_passed: bool | None = (
            None if min_views is None else views >= min_views)
        success_passed = str(target.get("status") or "") == "succeeded"
        replay_ok = bool(replay_block.get("ok"))
        checks = [
            {"id": "success", "passed": success_passed,
             "detail": {"status": target.get("status")}},
            {"id": "replay", "passed": replay_ok,
             "detail": {"ok": replay_ok,
                        "missing": list(replay_block.get("missing") or []),
                        "changed": list(replay_block.get("changed") or [])}},
            {"id": "programHash", "passed": hash_passed,
             "detail": {"observed": program_hash, "expected": expected}},
            {"id": "latency", "passed": latency_passed,
             "detail": {"durationMs": duration, "maxDurationMs": max_duration}},
            {"id": "cost", "passed": cost_passed,
             "detail": {"amount": amount, "maxCost": max_cost}},
            {"id": "views", "passed": views_passed,
             "detail": {"viewCount": views, "minViewCount": min_views}},
        ]
        dimensions = {
            "success": success_passed,
            "replay": replay_ok and hash_passed is not False,
            "latency": latency_passed is not False,
            "cost": cost_passed is not False,
            "views": views_passed is not False,
        }
        result["checks"] = checks
        result["replay"] = {
            "generationId": result["targetId"],
            "ok": replay_ok,
            "programHash": program_hash,
            "durationMs": duration,
            "cost": amount,
            "viewCount": views,
        }
        result["passed"] = all(dimensions.values())
        result["score"] = self._score(dimensions)
        return result

    def _error_case(self, base: dict, case: dict, target: dict | None,
                    error: str) -> dict:
        success = bool(target is not None
                       and str(target.get("status")) == "succeeded")
        constraints = (
            ("programHash", bool(case.get("expected_program_hash"))),
            ("latency", case.get("max_duration_ms") is not None),
            ("cost", case.get("max_cost") is not None),
            ("views", case.get("min_view_count") is not None),
        )
        checks = [
            {"id": "success", "passed": success,
             "detail": {"status": (target or {}).get("status")}},
            {"id": "replay", "passed": False, "detail": {"error": error}},
        ]
        for check_id, constrained in constraints:
            checks.append({
                "id": check_id,
                "passed": False if constrained else None,
                "detail": {"error": error,
                           "skipped": not constrained},
            })
        dimensions = {
            "success": success,
            "replay": False,
            "latency": not constraints[1][1],
            "cost": not constraints[2][1],
            "views": not constraints[3][1],
        }
        base.update({
            "checks": checks,
            "error": error,
            "passed": False,
            "targetId": (target or {}).get("id")
            or base.get("targetId") or "",
        })
        base["score"] = self._score(dimensions)
        return base

    @staticmethod
    def _score(dimensions: dict[str, bool]) -> float:
        total = sum(weight for name, weight in WEIGHTS.items()
                    if dimensions.get(name))
        return round(100 * total, 2)


__all__ = [
    "EVAL_SET_KIND",
    "EVAL_SET_STATUSES",
    "MAX_CASES",
    "WEIGHTS",
    "EvalSetUseCases",
]
