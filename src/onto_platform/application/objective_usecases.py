"""Application objectives (plan-v2 2.1).

An Application owns a business objective: one platform metric, a target, an
out-of-band tolerance and an evaluation window.  Evaluating the objective is
the slow loop's "what does good look like" contract:

    current value -> deviation from target -> trend over the window
                  -> in/out of band -> (out of band) open a handling todo

The objective lives on the Application aggregate, so showing the numbers is
part of the product page rather than an external dashboard.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext
from .events import EventLog
from .slo import ServiceLevels

#: metric -> which side of the target is healthy
OBJECTIVE_METRICS: dict[str, str] = {
    "success_rate": "higher_is_better",
    "unknown_rate": "lower_is_better",
    "pending_events": "lower_is_better",
    "projection_age_seconds": "lower_is_better",
}

DEFAULT_WINDOW_SECONDS = 3600
_MAX_SAMPLES = 200


def _as_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"objective {name} must be a number, got {value!r}")
    return float(value)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class SetObjective:
    """Declare (or replace) the application's objective (plan-v2 2.1)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, metric: str,
                 *, target: float, band: float = 0.0,
                 window: int = DEFAULT_WINDOW_SECONDS) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        if metric not in OBJECTIVE_METRICS:
            raise ValidationError(
                f"unknown objective metric {metric!r}; expected "
                f"{sorted(OBJECTIVE_METRICS)}")
        target = _as_number("target", target)
        band = _as_number("band", band)
        if band < 0:
            raise ValidationError("objective band must be >= 0")
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            raise ValidationError("objective window must be a positive integer")
        app = self._ctx.must_get("application", workspace_id, app_id)
        app["objective"] = {
            "metric": metric,
            "target": target,
            "band": band,
            "window": window,
            "direction": OBJECTIVE_METRICS[metric],
            "samples": [],
            "updated_at": self._ctx.clock(),
        }
        with self._ctx.transaction():
            self._ctx.save("application", app)
            EventLog(self._ctx).emit(
                "application.objective_set", workspace_id, actor,
                "application", app_id,
                payload={"metric": metric, "target": target, "band": band,
                         "window": window})
            self._ctx.audit_event(actor, workspace_id,
                                  "application.objective_set", app_id,
                                  {"metric": metric, "target": target})
        return app


class EvaluateObjective:
    """Compute current/deviation/trend and open a todo when out of band.

    Evaluation is an explicit write action (``POST .../objective/evaluate``)
    and a scheduler task.  ``GET`` returns the last evaluation and never
    silently appends samples or opens todos as a side effect of reading
    (P1-7).  ``peek`` is the read-only computation used by the controller.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str,
                 note: str = "") -> dict:
        if actor != "system":
            self._ctx.require(actor, workspace_id, "editor")
        evaluation = self._compute(workspace_id, app_id)
        if not evaluation.get("available"):
            return {**evaluation, "todo": None}
        app = self._ctx.must_get("application", workspace_id, app_id)
        objective = app.get("objective") or {}
        now = _parse_ts(self._ctx.clock()) or datetime.now(timezone.utc)
        samples = _prune_samples(objective.get("samples") or [], now,
                                 int(evaluation["window_seconds"]))
        samples.append({"at": now.isoformat(timespec="seconds"),
                        "value": evaluation["current"]})
        samples = samples[-_MAX_SAMPLES:]
        trend, trend_delta = _trend(samples)
        todo = self._open_todo_if_needed(
            workspace_id, actor, app, evaluation["objective"]["metric"],
            evaluation["objective"]["direction"], evaluation["current"],
            evaluation["target"], evaluation["band"],
            evaluation["deviation"], evaluation["ok"])
        objective["samples"] = samples
        objective["last_evaluation"] = {
            k: evaluation.get(k) for k in
            ("current", "target", "band", "deviation", "ok", "available")
        }
        objective["last_evaluation"].update({
            "trend": trend, "trend_delta": round(trend_delta, 6),
            "generated_at": self._ctx.clock()})
        objective["updated_at"] = self._ctx.clock()
        app["objective"] = objective
        with self._ctx.transaction():
            self._ctx.save("application", app)
        result = {**evaluation, "trend": trend,
                  "trend_delta": round(trend_delta, 6),
                  "samples": samples, "todo": todo,
                  "generated_at": self._ctx.clock()}
        return result

    def get(self, workspace_id: str, app_id: str, actor: str) -> dict:
        """Read the objective and its last evaluation without side effects."""
        self._ctx.require(actor, workspace_id, "viewer")
        app = self._ctx.must_get("application", workspace_id, app_id)
        objective = app.get("objective") or {}
        if not objective.get("metric"):
            raise ValidationError("application has no objective yet")
        last = objective.get("last_evaluation") or {}
        return {
            "application_id": app_id,
            "application_name": app.get("name", ""),
            "objective": {k: objective.get(k) for k in
                          ("metric", "target", "band", "window", "direction")},
            "last_evaluation": last or None,
            "samples": objective.get("samples") or [],
            "evaluated": bool(last),
            "current": last.get("current"),
            "target": objective.get("target"),
            "deviation": last.get("deviation"),
            "trend": last.get("trend"),
            "ok": last.get("ok"),
            "available": last.get("available"),
            "generated_at": last.get("generated_at"),
        }

    def evaluate_system(self, workspace_id: str, app_id: str) -> dict:
        """Internal scheduler entry point; uses ``system`` as the actor."""
        return self(workspace_id, app_id, "system")

    def peek(self, workspace_id: str, app_id: str) -> dict:
        """Read-only computation (no sample append, no todo, no save)."""
        return self._compute(workspace_id, app_id)

    def _compute(self, workspace_id: str, app_id: str) -> dict:
        app = self._ctx.must_get("application", workspace_id, app_id)
        objective = app.get("objective") or {}
        if not objective.get("metric"):
            raise ValidationError(
                "application has no objective yet; set one before evaluating")
        metric = objective["metric"]
        target = float(objective["target"])
        band = float(objective.get("band") or 0.0)
        window = int(objective.get("window") or DEFAULT_WINDOW_SECONDS)
        direction = objective.get("direction") or OBJECTIVE_METRICS.get(
            metric, "higher_is_better")
        report = ServiceLevels(self._ctx).evaluate(workspace_id,
                                                   window_seconds=window)
        indicator = report["indicators"].get(metric)
        if indicator is None:
            raise ValidationError(
                f"objective metric {metric!r} is not produced by the "
                "service-level evaluator")
        available = indicator.get("value") is not None
        current = float(indicator["value"]) if available else None
        deviation = (current - target) if available else None
        if not available:
            ok = False
        elif direction == "higher_is_better":
            ok = current >= target - band
        else:
            ok = current <= target + band
        return {
            "application_id": app_id,
            "objective": {k: objective[k] for k in
                          ("metric", "target", "band", "window", "direction")},
            "current": current,
            "target": target,
            "band": band,
            "deviation": round(deviation, 6) if deviation is not None else None,
            "ok": ok,
            "available": available,
            "reason": None if available else "no samples in the window",
            "window_seconds": window,
            "samples": objective.get("samples") or [],
            "todo": None,
        }

    # ---- internals ----

    def _open_todo_if_needed(self, workspace_id: str, actor: str, app: dict,
                             metric: str, direction: str, current: float,
                             target: float, band: float, deviation: float,
                             ok: bool) -> dict | None:
        if ok:
            return None
        open_todos = [
            d for d in self._ctx.repo.list("todo", workspace_id)
            if d.get("status") == "open"
            and d.get("application_id") == app["id"]
            and d.get("metric") == metric
        ]
        if open_todos:
            return open_todos[0]
        todo = {
            "id": f"todo_{self._ctx.id_factory()}",
            "workspace_id": workspace_id,
            "application_id": app["id"],
            "application_name": app.get("name", ""),
            "metric": metric,
            "title": f"{app.get('name', 'application')}: {metric} out of band",
            "detail": (f"current={current:.6g} target={target:.6g} "
                       f"band={band:.6g} deviation={deviation:.6g}"),
            "current": current,
            "target": target,
            "band": band,
            "deviation": round(deviation, 6),
            "status": "open",
            "created_at": self._ctx.clock(),
            "updated_at": self._ctx.clock(),
            "resolved_at": None,
            "resolved_by": None,
        }
        with self._ctx.transaction():
            self._ctx.save("todo", todo)
            EventLog(self._ctx).emit(
                "application.objective_breached", workspace_id, actor,
                "application", app["id"],
                payload={"metric": metric, "current": current, "target": target,
                         "band": band, "deviation": deviation,
                         "direction": direction, "todo_id": todo["id"]})
            self._ctx.audit_event(actor, workspace_id,
                                  "application.objective_breached", app["id"],
                                  {"metric": metric, "current": current,
                                   "deviation": deviation})
        return todo


class ListTodos:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str,
                 status: str = "open") -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        todos = self._ctx.repo.list("todo", workspace_id)
        if status == "all":
            return todos
        return [t for t in todos if t.get("status") == status]


class ResolveTodo:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, todo_id: str,
                 note: str = "") -> dict:
        from ..errors import GateError

        self._ctx.require(actor, workspace_id, "editor")
        todo = self._ctx.repo.get("todo", workspace_id, todo_id)
        if todo is None:
            raise NotFoundError(f"todo not found: {todo_id!r}")
        if todo.get("status") == "resolved":
            return todo
        # closing a todo is a claim about the world: re-read the objective
        # instead of trusting the operator's click (P1 close gate)
        application_id = todo.get("application_id")
        if application_id:
            latest = EvaluateObjective(self._ctx)(
                workspace_id, application_id, actor,
                note=note or "todo resolution re-check")
            if not latest.get("ok"):
                raise GateError(
                    "cannot resolve todo: the objective is still out of "
                    f"band (metric={todo.get('metric')!r}, "
                    f"value={latest.get('current')!r}, "
                    f"band={latest.get('band')!r})")
        todo["status"] = "resolved"
        todo["resolved_at"] = self._ctx.clock()
        todo["resolved_by"] = actor
        todo["resolution_note"] = note
        todo["updated_at"] = self._ctx.clock()
        with self._ctx.transaction():
            self._ctx.save("todo", todo)
            if todo.get("kind") == "controller_non_convergence":
                # closing the escalation explicitly re-arms auto-repair
                state = self._ctx.repo.get("controller_state", workspace_id,
                                           "controller") or {}
                self._ctx.save("controller_state", {
                    **state, "id": "controller", "workspace_id": workspace_id,
                    "consecutive_failures": 0, "stopped": False,
                    "rearmed_at": self._ctx.clock(),
                })
            EventLog(self._ctx).emit(
                "todo.resolved", workspace_id, actor, "todo", todo_id,
                payload={"application_id": todo.get("application_id"),
                         "metric": todo.get("metric"), "note": note})
            self._ctx.audit_event(actor, workspace_id, "todo.resolved", todo_id,
                                  {"metric": todo.get("metric"), "note": note})
        return todo


class ObjectiveUseCases:
    def __init__(self, ctx: ApplicationContext) -> None:
        self.set = SetObjective(ctx)
        self.evaluate = EvaluateObjective(ctx)
        self.get = EvaluateObjective(ctx).get
        self.list_todos = ListTodos(ctx)
        self.resolve_todo = ResolveTodo(ctx)


def _prune_samples(samples: list[dict], now: datetime, window: int) -> list[dict]:
    kept: list[dict] = []
    for sample in samples:
        ts = _parse_ts(sample.get("at"))
        if ts is None:
            continue
        if (now - ts).total_seconds() <= window:
            kept.append(sample)
    return kept


def _trend(samples: list[dict]) -> tuple[str, float]:
    if len(samples) < 2:
        return "flat", 0.0
    previous = float(samples[-2]["value"])
    latest = float(samples[-1]["value"])
    delta = latest - previous
    if abs(delta) < 1e-9:
        return "flat", 0.0
    return ("up" if delta > 0 else "down"), delta
