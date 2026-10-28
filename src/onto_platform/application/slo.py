"""Windowed service-level indicators for the closed loop (P0-4 / P1-7).

Every rate is a *windowed* measurement with an explicit sample count.  No
samples is ``no_data``, too few samples is ``insufficient``, and only a real
breach is ``breach``.  A dashboard must never show a perfect score for a
window that contains nothing.  Unknown outcomes are counted separately and
never as successes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..errors import ValidationError
from .common import ApplicationContext

DEFAULT_TARGETS: dict = {
    "success_rate": 0.99,
    "unknown_rate": 0.03,
    "freshness_seconds": 3600,
    "pending_events": 50,
}

DEFAULT_WINDOW_SECONDS = 3600
DEFAULT_MIN_SAMPLES = 20
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 30 * 24 * 3600


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now(ctx: ApplicationContext) -> datetime:
    parsed = _parse_ts(ctx.clock())
    return parsed or datetime.now(timezone.utc)


def _within(value: object, now: datetime, window_seconds: int) -> bool:
    ts = _parse_ts(value)
    if ts is None:
        return False
    return 0 <= (now - ts).total_seconds() <= window_seconds


class ServiceLevels:
    def __init__(self, ctx: ApplicationContext, targets: dict | None = None) -> None:
        self._ctx = ctx
        self.targets = {**DEFAULT_TARGETS, **(targets or {})}
        self.window_seconds = int(
            self.targets.pop("window_seconds", DEFAULT_WINDOW_SECONDS))
        self.min_samples = int(
            self.targets.pop("min_samples", DEFAULT_MIN_SAMPLES))

    def __call__(self, workspace_id: str, actor: str,
                 window_seconds: int | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return self.evaluate(workspace_id, window_seconds=window_seconds)

    def evaluate(self, workspace_id: str,
                 window_seconds: int | None = None) -> dict:
        window = _validate_window(window_seconds or self.window_seconds)
        now = _now(self._ctx)
        executions = [
            e for e in self._ctx.repo.list("execution", workspace_id)
            if _within(e.get("finished_at") or e.get("created_at")
                       or e.get("started_at"), now, window)
        ]
        succeeded = sum(1 for e in executions if e.get("status") == "succeeded")
        failed = sum(1 for e in executions if e.get("status") == "failed")
        unknown = sum(1 for e in executions if e.get("status") == "unknown")
        decided = succeeded + failed
        total = len(executions)
        indicators: dict[str, dict] = {
            "success_rate": _rate_indicator(
                succeeded / decided if decided else None,
                self.targets["success_rate"], ">=", decided,
                self.min_samples),
            "unknown_rate": _rate_indicator(
                unknown / total if total else None,
                self.targets["unknown_rate"], "<=", total,
                self.min_samples),
        }
        pending = self._pending_events(workspace_id)
        indicators["pending_events"] = _gauge_indicator(
            len(pending), self.targets["pending_events"], "<=", 1)
        runs = self._projection_runs(workspace_id)
        age = self._projection_age_seconds(runs, now)
        if not runs:
            indicators["projection_age_seconds"] = _not_applicable(
                "no projection configured")
        elif age is None:
            indicators["projection_age_seconds"] = _indicator(
                None, self.targets["freshness_seconds"], "<=", 0,
                status="no_data", reason="projection has never run")
        else:
            indicators["projection_age_seconds"] = _gauge_indicator(
                age, self.targets["freshness_seconds"], "<=", 1)
        breached = [
            name for name, item in indicators.items()
            if item["status"] in ("breach", "no_data", "insufficient")
        ]
        blocked = [name for name, item in indicators.items()
                   if item["status"] in ("no_data", "insufficient")]
        return {
            "workspace_id": workspace_id,
            "generated_at": self._ctx.clock(),
            "window_seconds": window,
            "targets": dict(self.targets),
            "indicators": indicators,
            "breached": breached,
            "blocked": blocked,
            "ok": (not breached and decided > 0
                   and indicators["success_rate"]["status"] == "ok"),
            "reason": (
                "no samples in the evaluation window" if not executions
                else "insufficient samples" if blocked
                else "one or more indicators breached" if breached
                else None),
        }

    def _pending_events(self, workspace_id: str) -> list[dict]:
        from .events import EventLog

        return EventLog(self._ctx).pending(workspace_id)

    def _projection_runs(self, workspace_id: str) -> list[dict]:
        return self._ctx.repo.list("projection_run", workspace_id)

    def _projection_age_seconds(self, runs: list[dict],
                                now: datetime) -> float | None:
        if not runs:
            return None
        checked = [r for r in runs if r.get("checked_at")
                   or r.get("finished_at")]
        if not checked:
            return None
        last = max(checked,
                   key=lambda r: r.get("checked_at")
                   or r.get("finished_at") or "")
        stamp = _parse_ts(last.get("checked_at")
                          or last.get("finished_at"))
        if stamp is None:
            return None
        return max(0.0, (now - stamp).total_seconds())


def _validate_window(value: object) -> int:
    try:
        window = int(value)
    except (TypeError, ValueError):
        raise ValidationError("window_seconds must be an integer") from None
    if window < MIN_WINDOW_SECONDS or window > MAX_WINDOW_SECONDS:
        raise ValidationError(
            f"window_seconds must be between {MIN_WINDOW_SECONDS} and "
            f"{MAX_WINDOW_SECONDS}")
    return window


def _indicator(value, target, direction: str, samples: int,
               status: str, reason: str | None = None,
               min_samples: int = DEFAULT_MIN_SAMPLES) -> dict:
    return {
        "value": value,
        "target": target,
        "direction": direction,
        "samples": samples,
        "min_samples": min_samples,
        "status": status,
        "available": status in ("ok", "breach"),
        "applicable": True,
        "ok": status == "ok",
        "reason": reason,
    }


def _rate_indicator(value, target, direction: str, samples: int,
                    min_samples: int) -> dict:
    if not samples:
        return _indicator(None, target, direction, 0, "no_data",
                          "no samples in the evaluation window",
                          min_samples=min_samples)
    if samples < min_samples:
        return _indicator(value, target, direction, samples, "insufficient",
                          f"{samples} samples, at least {min_samples} required",
                          min_samples=min_samples)
    ok = value >= target if direction == ">=" else value <= target
    return _indicator(value, target, direction, samples,
                      "ok" if ok else "breach", min_samples=min_samples)


def _gauge_indicator(value, target, direction: str, samples: int) -> dict:
    ok = value >= target if direction == ">=" else value <= target
    return _indicator(value, target, direction, samples,
                      "ok" if ok else "breach", min_samples=1)


def _not_applicable(reason: str) -> dict:
    item = _indicator(None, None, ">=", 0, "not_applicable", reason,
                      min_samples=0)
    item["applicable"] = False
    item["ok"] = True
    return item
