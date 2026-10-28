#!/usr/bin/env python3
"""End-to-end control-loop smoke test against the real HTTP adapter.

It builds an in-memory platform, seeds one traffic workspace, starts the
platform handler on a free loopback port and then exercises the contracts the
control-loop report found broken.  No network access and no external services
are required.  The process exits non-zero on the first failed assertion.

Run::

    python scripts/e2e_v03.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from http.server import ThreadingHTTPServer  # noqa: E402

from onto_platform.bootstrap import PlatformConfig, build_platform  # noqa: E402
from onto_platform.interfaces.api import build_handler  # noqa: E402

RECHARGE_SOURCE = """
def execute(ctx):
    vehicle = ctx.object()
    balance = vehicle.get("balance")
    new = balance + ctx.args["amount"]
    ctx.set(vehicle.ref, "balance", new)
    return {"success": True, "balance": new}
"""


def seed(platform) -> str:
    wid = platform.workspaces.create("高速运营", actor="admin")["id"]
    platform.ontologies.create(wid, "traffic", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"},
                    {"name": "balance", "type": "number"}])
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        properties=[{"name": "name", "type": "string"},
                    {"name": "status", "type": "string"}])
    platform.ontologies.upsert_relation(wid, "admin", "passes",
                                        "Vehicle", "TollStation")
    platform.ontologies.publish(wid, "admin")
    source = platform.data.connect_source(
        wid, "admin", "交易库", "static",
        assets=[
            {"name": "vehicles", "config": {"rows": [
                {"vid": "V001", "plate": "鲁A12345", "balance": 100},
                {"vid": "V002", "plate": "鲁B67890", "balance": 20}]}},
            {"name": "stations", "config": {"rows": [
                {"sid": "ST001", "name": "济南西收费站", "status": "congested"},
                {"sid": "ST002", "name": "青岛东收费站", "status": "free"}]}},
            {"name": "transactions", "config": {"rows": [
                {"vid": "V001", "sid": "ST001"},
                {"vid": "V002", "sid": "ST001"}]}},
        ])
    projection = platform.data.create_projection(
        wid, "admin", "主数据投影", source["id"],
        object_mappings=[
            {"asset": "vehicles", "object_type": "Vehicle", "id_field": "vid",
             "fields": [{"source_field": "plate", "property": "plate"},
                        {"source_field": "balance", "property": "balance"}]},
            {"asset": "stations", "object_type": "TollStation", "id_field": "sid",
             "fields": [{"source_field": "name", "property": "name"},
                        {"source_field": "status", "property": "status"}]},
        ],
        relation_mappings=[
            {"asset": "transactions", "relation": "passes",
             "source_field": "vid", "target_field": "sid"}])
    platform.data.run_projection(wid, projection["id"], "admin")
    capability = platform.capabilities.create(
        wid, "admin", key="recharge", name="车辆充值", target_type="Vehicle",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": RECHARGE_SOURCE})
    platform.capabilities.publish(wid, capability["id"], "admin")
    return wid


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.checks = 0

    def call(self, method: str, path: str, body: dict | None = None,
             expect: int = 200) -> dict:
        data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                payload = {"raw": raw.decode("utf-8", "replace")}
        if status != expect:
            raise AssertionError(
                f"{method} {path}: expected {expect}, got {status}: {payload}")
        self.checks += 1
        print(f"PASS {method} {path} -> {status}")
        return payload


def main() -> int:
    platform = build_platform(PlatformConfig(store="memory"))
    wid = seed(platform)
    empty = platform.workspaces.create("空工作空间", actor="admin")["id"]
    platform.start_scheduler(poll_seconds=60)
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 build_handler(platform))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    client = Client(base)
    try:
        health = client.call("GET", "/api/health")
        assert health["api_version"] == 3, health
        assert health["auth_mode"] == "local", health
        assert "scheduler" in health["features"], health

        slo = client.call("GET", f"/api/workspaces/{empty}/slo")
        assert slo["indicators"]["success_rate"]["status"] == "no_data", slo
        assert slo["ok"] is False, slo
        metrics = client.call(
            "GET", f"/api/workspaces/{wid}/metrics?window=5m")
        assert metrics["window_seconds"] == 300, metrics

        invoked = client.call(
            "POST", f"/api/workspaces/{wid}/invoke",
            {"user": "admin", "function": "Vehicle.recharge",
             "target": "Vehicle/V001", "args": {"amount": 1},
             "idempotency_key": "e2e-timeout", "timeout": 0}, expect=202)
        assert invoked["status"] == "unknown" and invoked["ok"] is False, invoked
        record = client.call(
            "GET",
            f"/api/workspaces/{wid}/executions/by-key/e2e-timeout")
        assert record["id"] == "key_e2e-timeout", record
        assert record["status"] in ("unknown", "succeeded"), record

        releases = client.call("GET", f"/api/workspaces/{wid}/releases")
        release = releases["releases"][-1]
        client.call(
            "POST", f"/api/workspaces/{wid}/releases/{release['id']}/canary",
            {"user": "admin"})
        blocked = client.call(
            "POST", f"/api/workspaces/{wid}/releases/canary/promote",
            {"user": "admin"}, expect=409)
        assert "check" in blocked["error"], blocked
        check = client.call(
            "POST", f"/api/workspaces/{wid}/releases/canary/check",
            {"user": "admin"})
        assert check["ok"] is True, check
        promoted = client.call(
            "POST", f"/api/workspaces/{wid}/releases/canary/promote",
            {"user": "admin", "require_healthy": False})
        assert promoted["canary"]["promoted"] is True, promoted

        config = client.call(
            "POST", f"/api/workspaces/{wid}/llm-config",
            {"user": "admin", "provider": "openai",
             "base_url": "https://example.invalid/v1", "model": "e2e-model",
             "api_key": "sk-e2e-secret"})
        assert "sk-e2e-secret" not in json.dumps(config), config
        assert config["has_key"] is True, config
        fetched = client.call(
            "GET", f"/api/workspaces/{wid}/llm-config?user=admin")
        assert "sk-e2e-secret" not in json.dumps(fetched), fetched

        projections = client.call(
            "GET", f"/api/workspaces/{wid}/projections?user=admin")
        projection_id = projections[0]["id"]
        run = client.call(
            "POST", f"/api/workspaces/{wid}/projections/{projection_id}/run",
            {"user": "admin"})
        run_doc = client.call(
            "GET",
            f"/api/workspaces/{wid}/projections/{projection_id}/runs/"
            f"{run['run_id']}?user=admin")
        assert run_doc["id"] == run["run_id"], run_doc
        assert run_doc["status"] in ("succeeded", "skipped"), run_doc

        scheduler = client.call("GET", "/api/scheduler")
        assert scheduler["running"] is True, scheduler
        assert scheduler["tasks"], scheduler
        assert all(t["next_run_at"] for t in scheduler["tasks"]), scheduler
        print(f"\n{client.checks} HTTP checks passed")
        return 0
    finally:
        platform.stop_scheduler()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
