import sys
sys.path.insert(0, 'src')

from onto_platform.bootstrap import PlatformConfig, build_platform

platform = build_platform(PlatformConfig(store="memory"))
ws = platform.workspaces.create("对账链路", actor="admin")
wid = ws["id"]

# a draft ontology is enough for a round (no publish -> no kernel needed)
platform.ontologies.create(wid, "traffic", actor="admin", description="对账本体")
platform.ontologies.upsert_object(
    wid, "admin", "Vehicle",
    properties=[{"name": "plate", "type": "string"}], description="车辆")

rid = platform.workflow.create_round(wid, "admin", {
    "name": "第 1 轮",
    "goal": "打通对账查询",
    "scope": {
        "summary": "只覆盖车辆",
        "metrics": [
            {"name": "对账差异笔数", "unit": "笔/月", "baseline": 120,
             "target": 10, "owner": "业务方甲"},
        ],
    },
})["id"]

cq = platform.workflow.add_cq(wid, "admin", rid, {
    "code": "CQ-01", "question": "有哪些车辆？", "priority": "P0",
    "type": "competency",
    "expected": {"kind": "row_count", "value": 1},
    "query": {"kind": "fixture", "object_type": "Vehicle"},
    "model_refs": ["Vehicle"],
    "owner": "业务方甲",
})

platform.workflow.record_metric_reading(
    wid, "admin", rid, {"metric": "对账差异笔数", "value": 60})

oversight = platform.workflow.oversight(wid, "admin", rid)
print("round:", oversight["round_name"], "| status:", oversight["status"])
print("responsibility:", oversight["responsibility"])
print("value_metrics:", oversight["value_metrics"])
print("blockers:", oversight["publish_check"]["blockers"][:2], "...")

# owner persisted on the CQ doc and flows into trace matrix
matrix = platform.workflow.trace_matrix(wid, "admin", rid)
print("trace_matrix owner:", matrix["rows"][0]["owner"])

print("OK: oversight + readings + owner work end-to-end without kernel")
