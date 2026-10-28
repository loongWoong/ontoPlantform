"""Phase 2.4 + 1.11: execution leases, read-only auditors, ABox sampling."""

from __future__ import annotations

import threading
import time

import pytest

from onto_platform.application.limits import LeaseManager
from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.errors import AccessDeniedError, ConcurrencyConflictError
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta

from conftest import seed_workspace

INCREMENT_SOURCE = """
def execute(ctx):
    import time
    obj = ctx.object()
    value = obj.get("value")
    time.sleep(0.02)          # widen the read-modify-write window
    ctx.set(obj.ref, "value", value + 1)
    return {"value": value + 1}
"""


def _counter_workspace(platform):
    wid = platform.workspaces.create("counters", actor="admin")["id"]
    platform.ontologies.create(wid, "counters", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Counter", [{"name": "value", "type": "number"}],
        identity=[])
    platform.ontologies.publish(wid, "admin")
    capability = platform.capabilities.create(
        wid, "admin", key="increment", name="自增", target_type="Counter",
        implementation={"kind": "python", "source": INCREMENT_SOURCE})
    platform.capabilities.publish(wid, capability["id"], "admin")
    platform.ctx.gateway_for(wid).apply_delta(
        SemanticDelta(upserts=(
            ObjectUpsert(type="Counter", id="C1", properties={"value": 0}),)))
    return wid


def test_execution_lease_prevents_lost_updates(platform):
    wid = _counter_workspace(platform)
    errors: list[Exception] = []

    def invoke():
        try:
            platform.capabilities.invoke(wid, "admin", "Counter.increment",
                                         target="Counter/C1")
        except Exception as exc:  # pragma: no cover - failure detail
            errors.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    counter = platform.ctx.gateway_for(wid).get_object("Counter/C1")
    assert counter["properties"]["value"] == 8


def test_lease_timeout_is_a_retryable_conflict():
    leases = LeaseManager(timeout=0.05)
    held = threading.Event()
    release = threading.Event()

    def holder():
        with leases.hold("Counter/C1"):
            held.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(1)
    try:
        with pytest.raises(ConcurrencyConflictError) as excinfo:
            with leases.hold("Counter/C1"):
                pass
        assert "retry" in str(excinfo.value)
    finally:
        release.set()
        thread.join()


def test_platform_admin_reads_every_workspace_but_cannot_write():
    platform = build_platform(PlatformConfig(
        extra={"platform_admins": ["auditor"]}))
    wid = seed_workspace(platform)
    # global read-only access works without membership
    metrics = platform.metrics(wid, "auditor")
    assert metrics["indicators"]["objects_total"] == 4
    assert platform.verifier.verify_workspace(wid, "auditor")["ok"] is True
    # writes still require membership in the target workspace
    with pytest.raises(AccessDeniedError):
        platform.capabilities.invoke(
            wid, "auditor", "Vehicle.recharge", target="Vehicle/V001",
            args={"amount": 1})
    with pytest.raises(AccessDeniedError):
        platform.ontologies.upsert_object(
            wid, "auditor", "Nope", [{"name": "x", "type": "string"}])


def test_verify_supports_deterministic_sampling(platform):
    wid = seed_workspace(platform)
    sampled = platform.verifier.verify_workspace(wid, "admin", sample=1)
    assert sampled["ok"] is True
    assert sampled["sampled"] is True
    assert sampled["sample_size"] == 1
    assert sampled["checked"] == 2  # one Vehicle + one TollStation
    full = platform.verifier.verify_workspace(wid, "admin")
    assert full["sampled"] is False
    assert full["checked"] == 4
