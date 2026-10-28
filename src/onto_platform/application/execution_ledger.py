"""Execution ledger: attempt claims, replay and resolution (P0-2, P1-4).

The ledger is the platform's execution truth layer.  It deliberately does
not conflate three things that used to share one bucket:

    running    the attempt owns the idempotency key and is still executing
    succeeded  a deterministic success (``ok`` is true)
    failed     a deterministic failure (precondition / postcondition / kernel)
    unknown    the outcome is not known; it must never be replayed as success
               and must never be retried automatically

The claim write is a compare-and-set (expected version 0) so two concurrent
requests with the same idempotency key cannot both execute.  The losing
request receives ``in_progress`` (HTTP 409) or ``unknown``, never a replay of
a result that does not exist yet.

``resolve`` is the only human/deterministic path out of ``unknown``: it asks
the operator (or a reconciler) whether the effect actually happened.  A
resolution never turns the record into success, because that would fabricate
an execution that the platform never observed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..errors import ConcurrencyConflictError, NotFoundError, ValidationError
from .common import ApplicationContext

CLAIMED = "claimed"
REPLAY = "replay"
IN_PROGRESS = "in_progress"
UNKNOWN = "unknown"

TERMINAL_STATUSES = frozenset({"succeeded", "failed"})


def args_fingerprint(args: dict | None) -> str:
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=False,
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def record_id_for(key: str) -> str:
    return f"key_{key}"


@dataclass(frozen=True)
class Claim:
    kind: str
    record: dict


class ExecutionLedger:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    # ---- claim / replay -------------------------------------------------

    def claim(self, workspace_id: str, actor: str, function: str,
              target: str | None, args: dict, key: str | None) -> Claim:
        """Claim an idempotency key or return the prior record."""
        fingerprint = args_fingerprint(args)
        if key is None:
            claim_id = f"exec_{self._ctx.id_factory()}"
            record = self._new_record(
                workspace_id, actor, function, target, args, key,
                claim_id, fingerprint)
            self._ctx.save("execution", record)
            return Claim(CLAIMED, record)

        record_id = record_id_for(key)
        existing = self._ctx.repo.get("execution", workspace_id, record_id)
        if existing is not None:
            return self._replay_or_conflict(existing, function, target,
                                            fingerprint)
        claim_id = f"exec_{self._ctx.id_factory()}"
        record = self._new_record(
            workspace_id, actor, function, target, args, key, claim_id,
            fingerprint)
        record["id"] = record_id
        try:
            self._ctx.save_versioned("execution", record, 0)
        except ConcurrencyConflictError:
            # another request won the claim between get and put
            existing = self._ctx.repo.get("execution", workspace_id, record_id)
            if existing is None:  # pragma: no cover - defensive
                raise
            return self._replay_or_conflict(existing, function, target,
                                            fingerprint)
        return Claim(CLAIMED, record)

    def _new_record(self, workspace_id: str, actor: str, function: str,
                    target: str | None, args: dict, key: str | None,
                    claim_id: str, fingerprint: str) -> dict:
        return {
            "id": f"exec_{self._ctx.id_factory()}",
            "workspace_id": workspace_id,
            "idempotency_key": key,
            "execution_id": claim_id,
            "claim_id": claim_id,
            "function": function,
            "target": target,
            "actor": actor,
            "args_fingerprint": fingerprint,
            "attempt": 1,
            "status": "running",
            "in_flight": True,
            "owner_instance": self._ctx.instance_id,
            "result": None,
            "verification": None,
            "created_at": self._ctx.clock(),
            "started_at": self._ctx.clock(),
            "updated_at": self._ctx.clock(),
        }

    def _replay_or_conflict(self, existing: dict, function: str,
                            target: str | None, fingerprint: str) -> Claim:
        # reusing one key for a different action is a caller bug, not a replay
        if (existing.get("function") != function
                or existing.get("target") != target
                or existing.get("args_fingerprint") != fingerprint):
            raise ValidationError(
                "idempotency key is already bound to a different invocation")
        status = existing.get("status")
        if status == "running" or existing.get("in_flight"):
            return Claim(IN_PROGRESS, existing)
        if status == "unknown":
            return Claim(UNKNOWN, existing)
        return Claim(REPLAY, existing)

    # ---- finalize -------------------------------------------------------

    def finalize(self, record: dict, result: dict, *,
                 status: str, verification: dict | None = None,
                 execution_id: str | None = None,
                 in_flight: bool = False) -> dict:
        """Write the terminal (or still-unknown) outcome of an attempt."""
        record_id = record["id"]
        workspace_id = record["workspace_id"]
        current = self._ctx.repo.get("execution", workspace_id, record_id)
        if current is None:  # pragma: no cover - claim must exist
            current = dict(record)
        current.update({
            "status": status,
            "in_flight": in_flight,
            "result": result,
            "verification": verification,
            "updated_at": self._ctx.clock(),
        })
        if execution_id:
            current["execution_id"] = execution_id
        if status in TERMINAL_STATUSES:
            current["finished_at"] = self._ctx.clock()
        # keep the CAS chain safe if another thread finalized first: the
        # record is keyed by the unique claim, so a reload is enough
        self._ctx.save("execution", current)
        return current

    def mark_unknown(self, record: dict, result: dict,
                     verification: dict | None = None) -> dict:
        return self.finalize(record, result, status="unknown",
                             verification=verification,
                             execution_id=record.get("execution_id"),
                             in_flight=True)

    def resolve(self, workspace_id: str, execution_id: str, actor: str,
                outcome: str, note: str = "") -> dict:
        """Resolve an unknown execution without pretending it succeeded."""
        if outcome not in ("applied", "not_applied"):
            raise ValidationError(
                "resolution outcome must be 'applied' or 'not_applied'")
        record = self.find(workspace_id, execution_id)
        if record is None:
            raise NotFoundError(f"execution not found: {execution_id!r}")
        if record.get("status") != "unknown":
            raise ValidationError(
                f"execution is {record.get('status')!r}, not unknown")
        if record.get("in_flight"):
            raise ValidationError(
                "execution is still in flight on a worker; resolve after it "
                "settles or restart the process")
        resolution = {
            "outcome": outcome,
            "note": note,
            "actor": actor,
            "at": self._ctx.clock(),
        }
        record["resolution"] = resolution
        record["resolved_at"] = resolution["at"]
        record["result"] = {**(record.get("result") or {}),
                            "resolution": resolution,
                            "ok": False}
        record["updated_at"] = self._ctx.clock()
        with self._ctx.transaction():
            self._ctx.save("execution", record)
            from .events import EventLog

            EventLog(self._ctx).emit(
                "execution.resolved", workspace_id, actor, "execution",
                record["id"], payload=resolution,
                execution_id=record.get("execution_id") or None)
            self._ctx.audit_event(actor, workspace_id, "execution.resolved",
                                  record["id"], resolution)
        return record

    # ---- read / recovery ------------------------------------------------

    def find(self, workspace_id: str, execution_id: str) -> dict | None:
        direct = self._ctx.repo.get("execution", workspace_id, execution_id)
        if direct is not None:
            return direct
        for record in self._ctx.repo.list("execution", workspace_id):
            if execution_id in (record.get("execution_id"),
                                record.get("claim_id")):
                return record
        return None

    def list(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("execution", workspace_id)

    def recover_stale(self, workspace_id: str | None = None) -> int:
        """Mark claims owned by a dead process unknown (process restart).

        An execution that was ``running`` when the process died may or may
        not have happened.  It is converted to ``unknown`` and left for an
        operator/reconciler to resolve; it is never retried automatically.
        """
        now = self._ctx.clock()
        recovered = 0
        for record in list(self._ctx.repo.list("execution", workspace_id)):
            if record.get("owner_instance") == self._ctx.instance_id:
                continue
            if record.get("in_flight") or record.get("status") == "running":
                record.update({
                    "status": "unknown",
                    "in_flight": False,
                    "error": "process restarted before the outcome was known",
                    "recovered_at": now,
                    "updated_at": now,
                })
                self._ctx.save("execution", record)
                recovered += 1
        return recovered
