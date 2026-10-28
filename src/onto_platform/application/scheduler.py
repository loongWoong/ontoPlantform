"""Single-process scheduler for the slow control loop (P1-1 / P1-11).

The scheduler is deliberately small: one process owns the timer loop, each
task is isolated (a failing task cannot stop the others), and every task is
idempotent because it delegates to the same audited use cases a human would
call.  Running two scheduler processes is not supported; the outbox claim in
``Dispatcher``/``ExecutionLedger`` is the concurrency boundary for requests,
not a distributed scheduler lock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .common import ApplicationContext


@dataclass
class ScheduledTask:
    name: str
    interval_seconds: float
    action: Callable[[], object]
    last_run_at: float = 0.0
    last_error: str | None = None
    runs: int = 0
    failures: int = 0
    #: wall-clock times surfaced by the ops API; ``last_run_at`` above stays
    #: monotonic because interval decisions must not jump with clock changes
    last_run_time: str | None = None
    next_run_time: str | None = None


@dataclass
class Scheduler:
    ctx: ApplicationContext
    tasks: list[ScheduledTask] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False

    def every(self, name: str, interval_seconds: float,
              action: Callable[[], object]) -> ScheduledTask:
        task = ScheduledTask(name=name, interval_seconds=max(1.0, interval_seconds),
                             action=action)
        task.next_run_time = self._plus(task.interval_seconds)
        self.tasks.append(task)
        return task

    def _plus(self, seconds: float) -> str:
        now = datetime.fromisoformat(self.ctx.clock())
        return (now + timedelta(seconds=seconds)).isoformat()

    def _mark_ran(self, task: ScheduledTask) -> None:
        task.last_run_time = self.ctx.clock()
        task.next_run_time = self._plus(task.interval_seconds)

    def tick(self, now: float | None = None) -> list[dict]:
        now = time.monotonic() if now is None else now
        results: list[dict] = []
        for task in list(self.tasks):
            if now - task.last_run_at < task.interval_seconds:
                continue
            task.last_run_at = now
            self._mark_ran(task)
            try:
                value = task.action()
            except Exception as exc:  # failure isolation
                task.failures += 1
                task.last_error = f"{type(exc).__name__}: {exc}"
                results.append({"task": task.name, "ok": False,
                                "error": task.last_error})
            else:
                task.runs += 1
                results.append({"task": task.name, "ok": True, "result": value})
        return results

    def run_once(self, name: str | None = None) -> list[dict]:
        """Run due tasks now (or one named task), useful for ops and tests."""
        now = time.monotonic()
        results: list[dict] = []
        for task in list(self.tasks):
            if name is not None and task.name != name:
                continue
            if name is None and now - task.last_run_at < task.interval_seconds:
                continue
            task.last_run_at = now
            self._mark_ran(task)
            try:
                value = task.action()
            except Exception as exc:  # failure isolation
                task.failures += 1
                task.last_error = f"{type(exc).__name__}: {exc}"
                results.append({"task": task.name, "ok": False,
                                "error": task.last_error})
            else:
                task.runs += 1
                results.append({"task": task.name, "ok": True, "result": value})
        return results

    def start(self, poll_seconds: float = 1.0) -> None:
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self.running = True
            self._thread = threading.Thread(
                target=self._loop, args=(max(0.2, poll_seconds),),
                name="onto-scheduler", daemon=True)
            self._thread.start()

    def _loop(self, poll_seconds: float) -> None:
        while not self._stop.wait(poll_seconds):
            self.tick()
        self.running = False

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self.running = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "tasks": [
                {"name": t.name, "interval_seconds": t.interval_seconds,
                 "runs": t.runs, "failures": t.failures,
                 "last_error": t.last_error,
                 "last_run_at": t.last_run_time,
                 "next_run_at": t.next_run_time}
                for t in self.tasks
            ],
        }
