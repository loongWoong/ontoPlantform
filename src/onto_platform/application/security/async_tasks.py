"""Deferred user actions with a durable, revalidated authorization context.

An HTTP request must not keep a thread alive for a slow capability.  A deferred
action is persisted as an ``async_task`` control-plane row before it is handed
to the scheduler.  The row carries the signed ``AuthorizationContext`` captured
at enqueue time; the worker rechecks the decision against the *current* policy
before it executes.  A restart can therefore rebuild the queue from storage
(``restore``), while editing the signed payload still invalidates the HMAC.

The queue is at-least-once: a task is marked ``running`` before invocation and
``done``/``failed`` afterwards.  A task left ``running`` by a process crash is
recovered as ``pending`` by :meth:`restore`, which is safe because capability
invocation is idempotent by the task id.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from ...errors import ValidationError
from . import context as auth_context

ASYNC_TASK_KIND = "async_task"


class AsyncTaskService:
    def __init__(self, ctx: Any, security: Any, scheduler: Any = None,
                 invoke: Any = None, *, clock: Any = None) -> None:
        self.ctx = ctx
        self.security = security
        self.scheduler = scheduler
        self._invoke = invoke
        self._clock = clock or time.time
        self._scheduled: set[str] = set()

    # ---- storage ---------------------------------------------------------

    def _store(self):
        return getattr(self.ctx.repo, "raw", self.ctx.repo)

    def _load(self, task_id: str) -> dict | None:
        row = self._store().get(ASYNC_TASK_KIND, self._workspace_of(task_id),
                                task_id)
        if row is not None:
            return row
        for candidate in self._store().list(ASYNC_TASK_KIND):
            if candidate.get("id") == task_id:
                return candidate
        return None

    def _workspace_of(self, task_id: str) -> str | None:
        # The task row is looked up by id; the workspace key is only a hint.
        for row in self._store().list(ASYNC_TASK_KIND):
            if row.get("id") == task_id:
                return str(row.get("workspace_id") or "") or None
        return None

    def _save(self, row: dict) -> None:
        self._store().put(ASYNC_TASK_KIND,
                          str(row.get("workspace_id") or "") or None,
                          str(row["id"]), row)

    def _scheduler(self):
        scheduler = self.scheduler() if callable(self.scheduler) \
            else self.scheduler
        if scheduler is None:
            raise ValidationError("scheduler is not running")
        return scheduler

    # ---- public API ------------------------------------------------------

    def enqueue(self, workspace_id: str, actor: str, function: str,
                *, target: str | None = None, args: dict | None = None,
                delay_seconds: float = 0.0,
                max_attempts: int = 3) -> dict:
        function = str(function or "").strip()
        if not function:
            raise ValidationError("function must not be empty")
        if not function.count("."):
            raise ValidationError("function must be '<ObjectType>.<capability>'")
        delay = max(0.0, min(float(delay_seconds or 0.0), 86400.0))
        # Enqueue is an action, not a read: the caller must be allowed to
        # invoke.  The snapshot is the evidence the worker revalidates.
        self.security.check_action(actor, workspace_id, "capability.invoke")
        authorization = auth_context.snapshot()
        if not authorization:
            raise ValidationError("no authorization context to serialize")
        task_id = f"async-{uuid.uuid4().hex[:12]}"
        now = float(self._clock())
        row = {
            "id": task_id,
            "kind": ASYNC_TASK_KIND,
            "workspace_id": workspace_id,
            "actor": actor,
            "function": function,
            "target": target,
            "args": dict(args or {}),
            "authorization": authorization,
            "status": "pending",
            "attempts": 0,
            "max_attempts": max(1, min(int(max_attempts), 10)),
            "due_at": now + delay,
            "created_at": self.ctx.clock(),
            "updated_at": self.ctx.clock(),
        }
        self._save(row)
        task = self._schedule(row)
        return {
            "id": task_id,
            "status": "scheduled",
            "action": function,
            "target": target,
            "delay_seconds": delay,
            "next_run_at": task.next_run_time,
            "authorization_bound": True,
        }

    def _schedule(self, row: dict):
        scheduler = self._scheduler()
        task_id = str(row["id"])
        if task_id in self._scheduled:
            return next((task for task in scheduler.tasks
                         if task.name == task_id), None)
        due = float(row.get("due_at") or self._clock())
        delay = max(0.0, due - float(self._clock()))
        task = scheduler.once(task_id, delay, lambda: self._execute(task_id),
                              authorization=row.get("authorization"))
        self._scheduled.add(task_id)
        return task

    def _execute(self, task_id: str) -> dict:
        row = self._load(task_id)
        if row is None:
            raise ValidationError(f"async task not found: {task_id!r}")
        if row.get("status") in ("done", "failed"):
            return {"id": task_id, "status": row["status"],
                    "skipped": True}
        row["status"] = "running"
        row["attempts"] = int(row.get("attempts") or 0) + 1
        row["started_at"] = self.ctx.clock()
        row["updated_at"] = self.ctx.clock()
        self._save(row)
        try:
            if self._invoke is None:
                raise ValidationError("no capability invoker configured")
            result = self._invoke(
                str(row["workspace_id"]), str(row["actor"]),
                str(row["function"]), target=row.get("target"),
                args=dict(row.get("args") or {}),
                idempotency_key=f"async:{task_id}")
        except Exception as exc:
            row["status"] = "failed"
            row["last_error"] = f"{type(exc).__name__}: {exc}"
            row["finished_at"] = self.ctx.clock()
            row["updated_at"] = row["finished_at"]
            self._save(row)
            raise
        row["status"] = "done"
        row["result"] = result if isinstance(result, (dict, list, str, int,
                                                      float, bool)) else None
        row["finished_at"] = self.ctx.clock()
        row["updated_at"] = row["finished_at"]
        self._save(row)
        return {"id": task_id, "status": "done", "result": result}

    def restore(self) -> list[dict]:
        """Rebuild one-shot scheduler entries from durable pending rows.

        A row left ``running`` by a killed process is reset to ``pending``:
        capability invocation is idempotent by the task id, so replaying is
        safe and silently dropping the work is not.
        """
        restored: list[dict] = []
        for row in self._store().list(ASYNC_TASK_KIND):
            status = str(row.get("status") or "")
            if status == "running":
                row["status"] = "pending"
                row["last_error"] = "recovered after process restart"
                row["updated_at"] = self.ctx.clock()
                self._save(row)
                status = "pending"
            if status != "pending":
                continue
            self._schedule(row)
            restored.append({"id": row["id"], "workspace_id":
                             row.get("workspace_id"),
                             "due_at": row.get("due_at")})
        return restored

    def list_tasks(self, workspace_id: str | None = None) -> list[dict]:
        rows = self._store().list(
            ASYNC_TASK_KIND, workspace_id or None)
        if workspace_id is None:
            rows = [row for row in self._store().list(ASYNC_TASK_KIND)]
        return sorted(rows, key=lambda row: row.get("created_at", ""),
                      reverse=True)

    def run_now(self, task_id: str) -> list[dict]:
        scheduler = self._scheduler()
        if not any(task.name == task_id for task in scheduler.tasks):
            row = self._load(task_id)
            if row is None:
                raise ValidationError(f"async task not found: {task_id!r}")
            self._schedule(row)
        return scheduler.run_once(task_id)
