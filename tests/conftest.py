"""Shared fixtures: a fully seeded traffic workspace (design.txt §48).

Fixture menu (v2 W0):
    platform          in-memory platform, empty
    workspace_id      seeded traffic workspace (ontology + projection + cap)
    empty_workspace   workspace with no ontology yet
    sqlite_platform   persistent platform (design-time store on disk)
    http_server       (base_url, workspace_id) REST server on a free port
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from onto_platform.bootstrap import PlatformConfig, build_platform

RECHARGE_SOURCE = """
def execute(ctx):
    vehicle = ctx.object()
    balance = vehicle.get("balance")
    new = balance + ctx.args["amount"]
    ctx.set(vehicle.ref, "balance", new)
    return {"success": True, "balance": new}
"""

VEHICLES = [
    {"vid": "V001", "plate": "鲁A12345", "balance": 100},
    {"vid": "V002", "plate": "鲁B67890", "balance": 20},
]
STATIONS = [
    {"sid": "ST001", "name": "济南西收费站", "status": "congested"},
    {"sid": "ST002", "name": "青岛东收费站", "status": "free"},
]
TRANSACTIONS = [
    {"vid": "V001", "sid": "ST001"},
    {"vid": "V002", "sid": "ST001"},
    {"vid": "V001", "sid": "ST002"},
]


def make_platform(**config):
    return build_platform(PlatformConfig(store="memory", **config))


def seed_workspace(platform):
    """workspace + published ontology + projection + recharge capability."""
    ws = platform.workspaces.create("高速运营", actor="admin")
    wid = ws["id"]
    platform.ontologies.create(wid, "traffic", actor="admin", description="高速本体")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string"},
                    {"name": "balance", "type": "number"}],
        description="车辆")
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        properties=[{"name": "name", "type": "string"},
                    {"name": "status", "type": "string"}],
        description="收费站")
    platform.ontologies.upsert_relation(wid, "admin", "passes", "Vehicle", "TollStation")
    platform.ontologies.add_term(wid, "admin", "收费站", "TollStation")
    platform.ontologies.add_term(wid, "admin", "车辆", "Vehicle")
    platform.ontologies.add_term(wid, "admin", "拥堵", "TollStation.status", kind="property")
    platform.ontologies.publish(wid, "admin")

    source = platform.data.connect_source(
        wid, "admin", "交易库", "static",
        assets=[
            {"name": "vehicles", "config": {"rows": VEHICLES}},
            {"name": "stations", "config": {"rows": STATIONS}},
            {"name": "transactions", "config": {"rows": TRANSACTIONS}},
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
             "source_field": "vid", "target_field": "sid"},
        ])
    platform.data.run_projection(wid, projection["id"], "admin")

    capability = platform.capabilities.create(
        wid, "admin", key="recharge", name="车辆充值", target_type="Vehicle",
        description="给车辆充值", inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": RECHARGE_SOURCE})
    platform.capabilities.publish(wid, capability["id"], "admin")
    return wid


@pytest.fixture()
def platform():
    return make_platform()


@pytest.fixture()
def workspace_id(platform):
    return seed_workspace(platform)


@pytest.fixture()
def empty_workspace(platform):
    """A workspace that exists but has no ontology/projection/capability."""
    return platform.workspaces.create("空工作空间", actor="admin")["id"]


@pytest.fixture()
def sqlite_platform(tmp_path):
    """A platform whose design-time store is a real SQLite file."""
    return build_platform(PlatformConfig(
        store="sqlite", db_path=str(tmp_path / "platform.db")))


@pytest.fixture()
def http_server(platform, workspace_id):
    """(base_url, workspace_id) with the real REST handler on a free port."""
    from onto_platform.interfaces.api import build_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, workspace_id
    httpd.shutdown()


def http_get(base: str, path: str):
    """GET JSON; returns (status, body) instead of raising on HTTP errors."""
    try:
        with urllib.request.urlopen(base + path) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def http_post(base: str, path: str, body: dict | None = None,
              headers: dict | None = None):
    """POST JSON; returns (status, body) instead of raising on HTTP errors."""
    request = urllib.request.Request(
        base + path, data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
