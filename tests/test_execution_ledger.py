"""Execution ledger semantics (P0-2 / P0-3)."""

from __future__ import annotations

import pytest

from conftest import make_platform, seed_workspace
from onto_platform.application.execution_ledger import ExecutionLedger


def _platform():
    platform = make_platform()
    wid = seed_workspace(platform)
    return platform, wid


def test_one_key_claims_once_and_replays_after_success():
    platform, wid = _platform()
    first = platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="ledger-replay")
    assert first["ok"] is True
    second = platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="ledger-replay")
    assert second["idempotent_replay"] is True
    assert second["execution_id"] == first["execution_id"]
    runs = [r for r in platform.ctx.repo.list("execution", wid)
            if r.get("idempotency_key") == "ledger-replay"]
    assert len(runs) == 1


def test_same_key_with_different_args_is_rejected():
    from onto_platform.errors import ValidationError

    platform, wid = _platform()
    platform.capabilities.invoke(
        wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
        args={"amount": 5}, idempotency_key="ledger-bind")
    with pytest.raises(ValidationError):
        platform.capabilities.invoke(
            wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
            args={"amount": 6}, idempotency_key="ledger-bind")


def test_unknown_requires_an_explicit_resolution():
    platform, wid = _platform()
    ledger = ExecutionLedger(platform.ctx)
    claim = ledger.claim(wid, "admin", "Vehicle.recharge", "Vehicle/V001",
                         {"amount": 1}, "ledger-unknown")
    ledger.mark_unknown(claim.record, {"status": "unknown", "ok": False})
    # a live worker still owns the effect: resolution must wait
    from onto_platform.errors import ValidationError

    with pytest.raises(ValidationError):
        ledger.resolve(wid, claim.record["id"], "admin", "applied")
    # simulate a restarted process: no worker owns it now
    record = platform.ctx.repo.get("execution", wid, claim.record["id"])
    record["in_flight"] = False
    platform.ctx.save("execution", record)
    resolved = ledger.resolve(wid, claim.record["id"], "admin", "applied",
                              note="checked the source database")
    assert resolved["status"] == "unknown"
    assert resolved["resolution"]["outcome"] == "applied"
    assert resolved["result"]["ok"] is False
    assert platform.events.list(wid, event_type="execution.resolved")


def test_one_hundred_concurrent_calls_for_one_key_execute_once():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from onto_platform.errors import ExecutionInProgressError

    platform, wid = _platform()
    slow = platform.capabilities.create(
        wid, "admin", key="slow", name="慢动作", target_type="Vehicle",
        implementation={"kind": "python", "source": (
            "import time\n"
            "def execute(ctx):\n"
            "    time.sleep(2.0)\n"
            "    return {'ok': True}\n")})
    platform.capabilities.publish(wid, slow["id"], "admin")
    barrier = threading.Barrier(100)

    def call(_):
        barrier.wait()
        try:
            result = platform.capabilities.invoke(
                wid, "admin", "Vehicle.slow", target="Vehicle/V001",
                idempotency_key="ledger-concurrent")
            return result["status"]
        except ExecutionInProgressError:
            return "in_progress"

    with ThreadPoolExecutor(max_workers=100) as pool:
        statuses = list(pool.map(call, range(100)))
    assert statuses.count("in_progress") == 99
    assert statuses.count("succeeded") == 1
    records = [r for r in platform.ctx.repo.list("execution", wid)
               if r.get("idempotency_key") == "ledger-concurrent"]
    assert len(records) == 1


def test_stale_claim_from_a_dead_process_becomes_unknown():
    platform, wid = _platform()
    platform.ctx.repo.put("execution", wid, "key_stale", {
        "id": "key_stale", "workspace_id": wid, "status": "running",
        "in_flight": True, "owner_instance": "inst_dead_process",
        "execution_id": "exec_stale",
    })
    recovered = ExecutionLedger(platform.ctx).recover_stale(wid)
    assert recovered == 1
    record = platform.ctx.repo.get("execution", wid, "key_stale")
    assert record["status"] == "unknown"
    assert record["in_flight"] is False
