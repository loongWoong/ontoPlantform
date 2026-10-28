"""Layered controller (plan-v2 2.2): propose -> arbitrate -> verify.

The fast loop already lives in the runtime gateway (bounded invocation slots,
timeout -> ``unknown``).  This module is the *slow* closed loop around it:

    observe (reconciler / SLO)
      -> propose   deterministic repair actions
      -> arbitrate policy decides which proposals are safe to apply
      -> act       execute the accepted action through its owning use case
      -> verify    re-observe and report whether the world converged

Nothing here mutates state directly: proposals are executed through the same
audited use cases a human would call, so governance and events still apply.
"""

from __future__ import annotations

from .common import ApplicationContext
from .verifier import Reconciler

#: after this many failed repair cycles the controller stops and asks a human
#: rather than looping forever (stability, not busy retry)
MAX_CONSECUTIVE_FAILURES = 3


class DriftController:
    """Deterministic slow-loop controller for projection drift."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx
        self._reconciler = Reconciler(ctx)

    def observe(self, workspace_id: str, actor: str = "system",
                emit: bool = False) -> dict:
        """Read-only observation: reconcile source of truth vs runtime."""
        return self._reconciler.reconcile(workspace_id, actor, emit=emit)

    def propose(self, workspace_id: str, actor: str = "system") -> dict:
        """Deterministic proposals derived from observed drift and objectives."""
        report = self.observe(workspace_id, actor)
        proposals: list[dict] = []
        if not report["ok"]:
            projection_ids = {
                finding["projection_id"]
                for finding in report["findings"]
                if finding.get("kind") == "projection-drift"
                and finding.get("projection_id")
            }
            proposals = [
                {
                    "action": "run_projection",
                    "projection_id": projection_id,
                    "reason": "source-of-truth rows differ from the runtime",
                }
                for projection_id in sorted(projection_ids)
            ]
        proposals.extend(self._objective_proposals(workspace_id, actor))
        proposals.extend(self._unknown_execution_proposals(workspace_id))
        return {"ok": report["ok"], "proposals": proposals, "report": report}

    def _objective_proposals(self, workspace_id: str,
                             actor: str) -> list[dict]:
        proposals: list[dict] = []
        for app in self._ctx.repo.list("application", workspace_id):
            objective = app.get("objective") or {}
            if not objective.get("metric"):
                continue
            try:
                from .objective_usecases import EvaluateObjective

                result = EvaluateObjective(self._ctx).peek(workspace_id,
                                                           app["id"])
            except Exception:
                continue
            if not result.get("ok"):
                proposals.append({
                    "action": "evaluate_objective",
                    "application_id": app["id"],
                    "metric": objective["metric"],
                    "reason": "objective is out of band",
                })
        return proposals

    def _unknown_execution_proposals(self, workspace_id: str) -> list[dict]:
        proposals: list[dict] = []
        for record in self._ctx.repo.list("execution", workspace_id):
            if record.get("status") == "unknown" and not record.get("resolution"):
                proposals.append({
                    "action": "resolve_unknown_execution",
                    "execution_id": record.get("execution_id"),
                    "record_id": record.get("id"),
                    "reason": "outcome unknown; a human must confirm whether "
                              "the effect happened",
                })
        return proposals

    def arbitrate(self, proposals: list[dict],
                  allow: set[str] | None = None) -> list[dict]:
        """Accept only known, idempotent repair actions (deduplicated).

        The default policy is deliberately narrow: re-running a projection is
        an idempotent state sync.  Anything else needs an explicit allow-list
        from the operator.
        """
        allowed = {"run_projection"} if allow is None else set(allow)
        accepted: list[dict] = []
        seen: set[tuple] = set()
        for proposal in proposals:
            action = proposal.get("action")
            target = proposal.get("projection_id")
            if action not in allowed:
                continue
            key = (action, target)
            if key in seen:
                continue
            seen.add(key)
            accepted.append(proposal)
        return accepted

    def act(self, workspace_id: str, actor: str, *,
            accept: bool = True, allow: set[str] | None = None) -> dict:
        """Run one control cycle: propose, arbitrate, act, verify."""
        if actor != "system":
            self._ctx.require(actor, workspace_id, "editor")
        state = self._ctx.repo.get("controller_state", workspace_id,
                                   "controller") or {}
        if state.get("stopped"):
            return {
                "proposed": [], "accepted": [], "applied": [],
                "verified": False, "converged": False, "escalated": True,
                "stopped": True,
                "reason": "auto-repair stopped after repeated non-convergence; "
                          "a human must resolve the open todo",
            }
        proposal = self.propose(workspace_id, actor)
        accepted = self.arbitrate(proposal["proposals"], allow) if accept else []
        applied: list[dict] = []
        for item in accepted:
            if item["action"] == "run_projection":
                from .data_usecases import RunProjection

                result = RunProjection(self._ctx)(
                    workspace_id, item["projection_id"], actor, force=True)
                applied.append({"action": item["action"],
                                "projection_id": item["projection_id"],
                                "run": result})
        verification = self.observe(workspace_id, actor)
        cycle = {
            "proposed": len(proposal["proposals"]),
            "accepted": len(accepted),
            "rejected": len(proposal["proposals"]) - len(accepted),
            "converged": verification["ok"],
        }
        self._ctx.audit_event(actor, workspace_id, "controller.cycle",
                              workspace_id, cycle)
        from .events import EventLog

        events = EventLog(self._ctx)
        consecutive = 0 if verification["ok"] else int(
            state.get("consecutive_failures") or 0) + 1
        stopped = consecutive >= MAX_CONSECUTIVE_FAILURES
        cycle["consecutive_failures"] = consecutive
        cycle["stopped"] = stopped
        if verification["ok"]:
            events.emit("controller.converged", workspace_id, actor,
                        "controller", workspace_id, payload=cycle)
        else:
            # the controller must not claim success when observation still
            # differs; escalate instead of silently ending the cycle
            cycle["escalated"] = True
            events.emit("controller.escalated", workspace_id, actor,
                        "controller", workspace_id,
                        payload={**cycle,
                                 "remaining": verification["findings"]})
            if stopped:
                self._open_escalation_todo(workspace_id, actor, verification,
                                           consecutive)
        with self._ctx.transaction():
            self._ctx.save("controller_state", {
                "id": "controller", "workspace_id": workspace_id,
                "consecutive_failures": consecutive,
                "stopped": stopped,
                "last_cycle_at": self._ctx.clock(),
            })
        return {
            "proposed": proposal["proposals"],
            "accepted": accepted,
            "applied": applied,
            "verified": verification["ok"],
            "converged": verification["ok"],
            "escalated": bool(cycle.get("escalated")),
            "stopped": stopped,
            "consecutive_failures": consecutive,
            "verification": verification,
            "cycle": cycle,
        }

    def _open_escalation_todo(self, workspace_id: str, actor: str,
                              verification: dict, rounds: int) -> dict | None:
        existing = [t for t in self._ctx.repo.list("todo", workspace_id)
                    if t.get("kind") == "controller_non_convergence"
                    and t.get("status") == "open"]
        if existing:
            return existing[0]
        todo = {
            "id": f"todo_{self._ctx.id_factory()}",
            "workspace_id": workspace_id,
            "kind": "controller_non_convergence",
            "status": "open",
            "metric": "projection_drift",
            "subject": "控制器连续多轮未收敛，已停止自动修复",
            "detail": "请人工检查投影源与运行时差异，处理后关闭待办以恢复自动控制。",
            "consecutive_failures": rounds,
            "findings": verification.get("findings", []),
            "opened_at": self._ctx.clock(),
        }
        with self._ctx.transaction():
            self._ctx.save("todo", todo)
            EventLog(self._ctx).emit(
                "todo.opened", workspace_id, actor, "todo", todo["id"],
                payload={"kind": todo["kind"], "rounds": rounds})
        return todo
