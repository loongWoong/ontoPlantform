"""Shared fixtures: a fully seeded traffic workspace (design.txt §48)."""

from __future__ import annotations

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
