"""Deferred user actions with a serialized, revalidated authorization context.

An HTTP request must not keep a thread alive for a slow capability.  A deferred
action is queued on the in-process scheduler together with the signed
``AuthorizationContext`` captured at enqueue time; the worker rechecks the
decision against the *current* policy before it executes.  Nothing in the task
payload is a bearer credential, and editing the payload invalidates the HMAC.
"""

from __future__ import annotations

import uuid
from typing import Any

from ...errors import ValidationError
from . import context as auth_context


class AsyncTaskService:
    def __init__(self, ctx: Any, security: Any, scheduler: Any = None,
                 invoke: Any = None) -> None:
        self.ctx = ctx
        self.security = security
        self.scheduler = scheduler
        self._invoke = invoke

    def _scheduler(self):
        scheduler = self.scheduler() if callable(self.scheduler) \
            else self.scheduler
        if scheduler is None:
            raise ValidationError("scheduler is not running")
        return scheduler

    def enqueue(self, workspace_id: str, actor: str, function: str,
                *, target: str | None = None, args: dict | None = None,
                delay_seconds: float = 0.0) -> dict:
        function = str(function or "").strip()
        if not function:
            raise ValidationError("function must not be empty")
        if not str(function).count("."):
            raise ValidationError("function must be '<ObjectType>.<capability>'")
        delay = max(0.0, min(float(delay_seconds or 0.0), 86400.0))
        # Enqueue is an action, not a read: the caller must be allowed to
        # invoke.  The snapshot is the evidence the worker revalidates.
        self.security.check_action(actor, workspace_id, "capability.invoke")
        authorization = auth_context.snapshot()
        if not authorization:
            raise ValidationError("no authorization context to serialize")
        scheduler = self._scheduler()
        task_name = f"async-{uuid.uuid4().hex[:12]}"

        def action():
            # The scheduler already activated/revalidated ``authorization``;
            # invoking through the normal use case keeps governance, the
            # execution ledger and audit identical to a synchronous call.
            if self._invoke is None:
                raise ValidationError("no capability invoker configured")
            return self._invoke(
                workspace_id, actor, function, target=target,
                args=args or {},
                idempotency_key=f"async:{task_name}")

        task = scheduler.once(task_name, delay, action,
                              authorization=authorization)
        return {
            "id": task_name,
            "status": "scheduled",
            "action": function,
            "target": target,
            "delay_seconds": delay,
            "next_run_at": task.next_run_time,
            "authorization_bound": True,
        }

    def list_tasks(self) -> list[dict]:
        scheduler = self._scheduler()
        return [
            {"id": task.name, "one_shot": task.one_shot,
             "delay_seconds": task.interval_seconds,
             "next_run_at": task.next_run_time,
             "runs": task.runs, "failures": task.failures,
             "last_error": task.last_error,
             "authorization_bound": bool(task.authorization)}
            for task in scheduler.tasks if task.one_shot
        ]

    def run_now(self, task_id: str) -> list[dict]:
        return self._scheduler().run_once(task_id)
