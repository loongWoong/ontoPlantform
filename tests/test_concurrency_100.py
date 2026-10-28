"""0.1 exit criterion: three concurrency scenarios, 100 rounds, 0 failures.

Plan-v2 §Phase 0 exit: "3 条并发测试 100 次循环 0 失败".  Each round starts
from a Barrier so the two threads really contend; the assertions are about
*invariants* (no lost update, exactly-once effect, no cross-object damage)
rather than about scheduling, so the test is deterministic.
"""

from __future__ import annotations

import threading

import pytest

from onto_platform.errors import ConcurrencyConflictError

from conftest import seed_workspace

ROUNDS = 100


def _ontology_version(platform, wid: str, ontology_id: str) -> int:
    _, version = platform.ctx.repo.get_versioned("ontology", wid, ontology_id)
    return version


def test_two_editors_hundred_rounds_never_lose_updates(platform):
    """Optimistic lock: every round has exactly one winner and one 409."""
    wid = platform.workspaces.create("race", actor="admin")["id"]
    platform.ontologies.create(wid, "race", "admin")
    ontology_id = platform.ontologies.get(wid, "admin")["id"]

    for round_no in range(ROUNDS):
        version = _ontology_version(platform, wid, ontology_id)
        barrier = threading.Barrier(2)
        results: list[str] = []
        errors: list[BaseException] = []

        def writer(name: str) -> None:
            barrier.wait()
            try:
                platform.ontologies.upsert_object(
                    wid, "admin", f"T{round_no}{name}",
                    [{"name": "code", "type": "string"}],
                    expected_version=version)
                results.append("ok")
            except ConcurrencyConflictError:
                results.append("conflict")
            except BaseException as exc:  # surface unexpected failures
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(name,))
                   for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors
        assert sorted(results) == ["conflict", "ok"], (round_no, results)
        assert _ontology_version(platform, wid, ontology_id) == version + 1


def test_same_idempotency_key_hundred_rounds_applies_once(platform):
    """One key = one effect, no matter how many times it is replayed."""
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    old = gateway.get_object("Vehicle/V001")["properties"]["balance"]
    for _ in range(ROUNDS):
        result = platform.capabilities.invoke(
            wid, "admin", "Vehicle.recharge", target="Vehicle/V001",
            args={"amount": 5}, idempotency_key="same-key")
        assert result["status"] == "succeeded"
    assert gateway.get_object("Vehicle/V001")["properties"]["balance"] == old + 5
    executions = [e for e in platform.ctx.repo.list("execution", wid)
                  if e.get("idempotency_key") == "same-key"]
    assert len(executions) == 1


def test_concurrent_writes_do_not_clobber_other_objects(platform):
    """100 rounds of two threads writing different objects: both persist."""
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    old = {ref: gateway.get_object(f"Vehicle/{ref}")["properties"]["balance"]
           for ref in ("V001", "V002")}
    for _ in range(ROUNDS):
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def recharge(ref: str, amount: int) -> None:
            barrier.wait()
            try:
                platform.capabilities.invoke(
                    wid, "admin", "Vehicle.recharge", target=f"Vehicle/{ref}",
                    args={"amount": amount})
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=recharge, args=("V001", 1)),
                   threading.Thread(target=recharge, args=("V002", 2))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors

    assert gateway.get_object("Vehicle/V001")["properties"]["balance"] == \
        old["V001"] + ROUNDS
    assert gateway.get_object("Vehicle/V002")["properties"]["balance"] == \
        old["V002"] + 2 * ROUNDS
