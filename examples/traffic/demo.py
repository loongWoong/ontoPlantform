"""高速收费站拥堵分析 —— OntoPlatform 完整业务实例 (design.txt §48)。

一条命令跑通平台的全部七个领域：

    workspace  ->  ontology  ->  data  ->  capability
        ->  application  ->  intelligence  ->  governance

    python examples/traffic/demo.py

运行结束后可以用 CLI 查看同一个世界:

    onto --db .onto/demo.db state find TollStation --filter status=congested
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from onto_platform.bootstrap import PlatformConfig, build_platform  # noqa: E402
from onto_platform.infrastructure.llm.echo import EchoLLMProvider  # noqa: E402

# ----------------------------------------------------------------------
# 能力源码：业务人员用 Python 表达"车辆充值"这条业务规则。
# 它经 FileCodeStore 落盘，发布后由 Kernel 的 FunctionExecutor 执行，
# 对状态的每次修改都成为 Kernel Change。
# ----------------------------------------------------------------------
RECHARGE_SOURCE = """
def execute(ctx):
    vehicle = ctx.object()
    new_balance = vehicle.get("balance") + ctx.args["amount"]
    ctx.set(vehicle.ref, "balance", new_balance)
    return {"success": True, "balance": new_balance}
"""

# 拥堵分析能力不写代码，用 workflow 编排既有观察：
# 进站车辆 -> 计数 -> 写回状态 -> 输出结论
CONGESTION_WORKFLOW = {
    "kind": "workflow",
    "steps": [
        {"kind": "related", "ref": "{{target}}", "relation": "passes",
         "direction": "in", "as": "vehicles"},
        {"kind": "count", "list": "{{steps.vehicles}}", "as": "vehicle_count"},
        {"kind": "get", "ref": "{{target}}", "as": "station"},
        {"kind": "set", "name": "last_vehicle_count",
         "value": "{{steps.vehicle_count}}"},
        {"kind": "set", "name": "congestion_report",
         "value": "{{steps.station.properties.name}} 近1小时通行 "
                  "{{steps.vehicle_count}} 辆车", "as": "output"},
    ],
}


def banner(title: str) -> None:
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


def main() -> None:
    demo_db = Path(".onto/demo.db")
    if demo_db.exists():
        demo_db.unlink()
    platform = build_platform(PlatformConfig(store="sqlite", db_path=str(demo_db)))

    # ---------------------------------------------------------- 1 workspace
    banner("Step 0 · 工作空间（隔离容器 + 成员治理）")
    ws = platform.workspaces.create("高速运营", actor="admin",
                                    description="山东高速示范工作空间")
    wid = ws["id"]
    platform.workspaces.add_member(wid, "analyst", "viewer", "admin")
    print(f"workspace: {ws['name']} ({wid})  成员: admin(owner) analyst(viewer)")

    # ---------------------------------------------------------- 2 ontology
    banner("Step 1 · 本体：业务世界的概念模型（design.txt §48 Step1）")
    platform.ontologies.create(wid, "traffic", actor="admin", description="高速收费本体")
    platform.ontologies.upsert_object(wid, "admin", "Vehicle", description="车辆",
                                      properties=[{"name": "plate", "type": "string"},
                                                  {"name": "balance", "type": "number"}])
    platform.ontologies.upsert_object(wid, "admin", "TollStation", description="收费站",
                                      properties=[{"name": "name", "type": "string"},
                                                  {"name": "status", "type": "string"},
                                                  {"name": "last_vehicle_count", "type": "integer"}])
    platform.ontologies.upsert_object(wid, "admin", "Road", description="路段",
                                      properties=[{"name": "name", "type": "string"}])
    platform.ontologies.upsert_object(wid, "admin", "TrafficFlow", description="流量",
                                      properties=[{"name": "hour", "type": "string"},
                                                  {"name": "count", "type": "integer"}])
    platform.ontologies.upsert_relation(wid, "admin", "passes", "Vehicle", "TollStation")
    platform.ontologies.upsert_relation(wid, "admin", "belongs_to", "TollStation", "Road")
    platform.ontologies.upsert_relation(wid, "admin", "measures", "TrafficFlow", "TollStation")
    # 语义层：业务语言 -> 本体概念（Ask/Agent 都靠它，不靠猜表名）
    platform.ontologies.add_term(wid, "admin", "收费站", "TollStation")
    platform.ontologies.add_term(wid, "admin", "车辆", "Vehicle")
    platform.ontologies.add_term(wid, "admin", "路段", "Road")
    platform.ontologies.add_term(wid, "admin", "拥堵", "TollStation.status", kind="property")
    problems = platform.ontologies.validate(wid, "admin")
    print(f"对象 4 个 / 关系 3 条 / 术语 4 条，校验: {'通过' if not problems else problems}")
    platform.ontologies.publish(wid, "admin")
    print("已发布 -> Kernel Schema（设计时 -> 运行时的唯一大门）")

    # ---------------------------------------------------------- 3 data
    banner("Step 2 · 数据：交易库投影进 Kernel（design.txt §48 Step2）")
    source = platform.data.connect_source(
        wid, "admin", "交易数据库", "static",
        assets=[
            {"name": "vehicles", "config": {"rows": [
                {"vid": "V001", "plate": "鲁A12345", "balance": 100},
                {"vid": "V002", "plate": "鲁B67890", "balance": 20},
                {"vid": "V003", "plate": "鲁C·00001", "balance": 55},
            ]}},
            {"name": "stations", "config": {"rows": [
                {"sid": "ST001", "name": "济南西收费站", "status": "congested"},
                {"sid": "ST002", "name": "青岛东收费站", "status": "free"},
            ]}},
            {"name": "transactions", "config": {"rows": [
                {"vid": "V001", "sid": "ST001"}, {"vid": "V002", "sid": "ST001"},
                {"vid": "V003", "sid": "ST001"}, {"vid": "V001", "sid": "ST002"},
            ]}},
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
    summary = platform.data.run_projection(wid, projection["id"], "admin")
    print(f"投影完成: {summary['objects_upserted']} 对象 / "
          f"{summary['links_asserted']} 关系边 / {summary['changes']} 变更")

    # ---------------------------------------------------------- 4 capability
    banner("Step 3 · 能力：会改变世界的行为（design.txt §48 Step3）")
    recharge = platform.capabilities.create(
        wid, "admin", key="recharge", name="车辆充值", target_type="Vehicle",
        description="给车辆充值并更新余额",
        inputs=[{"name": "amount", "type": "number"}],
        implementation={"kind": "python", "source": RECHARGE_SOURCE})
    congestion = platform.capabilities.create(
        wid, "admin", key="congestion_report", name="收费站拥堵分析",
        target_type="TollStation", description="统计通行车辆并生成报告",
        implementation=CONGESTION_WORKFLOW)
    for capability in (recharge, congestion):
        platform.capabilities.publish(wid, capability["id"], "admin")
    print(f"已发布能力: {[c['name'] for c in platform.capabilities.list(wid, 'admin')]}")

    result = platform.capabilities.invoke(wid, "admin", "Vehicle.recharge",
                                          target="Vehicle/V002", args={"amount": 80})
    print(f"调用 Vehicle.recharge(V002, +80) -> {result['output']}")
    report = platform.capabilities.invoke(wid, "admin", "TollStation.congestion_report",
                                          target="TollStation/ST001", args={})
    print(f"调用 TollStation.congestion_report(ST001) -> {report['output']}")

    # ---------------------------------------------------------- 5 application
    banner("Step 4 · 应用：面向业务人员的视图（design.txt §48 Step4）")
    app = platform.applications.create(wid, "admin", "收费站运行监控",
                                       description="高速收费站运行大盘")
    platform.applications.add_query(wid, app["id"], "admin", "congested_stations",
                                    "TollStation", filters={"status": "congested"})
    platform.applications.add_query(wid, app["id"], "admin", "all_stations", "TollStation")
    platform.applications.add_view(wid, app["id"], "admin", "station_list",
                                   title="收费站列表", kind="table",
                                   object_type="TollStation",
                                   query_alias="all_stations")
    platform.applications.add_view(wid, app["id"], "admin", "congestion",
                                   title="拥堵趋势", kind="table",
                                   object_type="TollStation",
                                   query_alias="congested_stations")
    platform.applications.attach_capability(wid, app["id"], "admin", recharge["id"])
    platform.applications.attach_capability(wid, app["id"], "admin", congestion["id"])
    platform.applications.publish(wid, app["id"], "admin")
    rows = platform.applications.run_query(wid, app["id"], "admin", "congested_stations")
    print(f"应用已发布，拥堵列表视图返回: {[r['id'] for r in rows['objects']]}")

    # ---------------------------------------------------------- 6 intelligence
    banner("Step 5 · 智能：Ask + Agent（design.txt §48 Step5）")
    answer = platform.intelligence.ask(wid, "analyst", "济南西收费站现在为什么拥堵？")
    print(f"\n[Ask] 济南西收费站现在为什么拥堵？")
    print(answer["answer"])

    script = [
        '{"think": "先找到拥堵的收费站", "action": {"tool": "find", "args": '
        '{"object_type": "TollStation", "filter": {"status": "congested"}}}}',
        '{"think": "看它最近的分析报告", "action": {"tool": "get", "args": '
        '{"ref": "TollStation/ST001"}}}',
        '{"think": "再调用拥堵分析能力确认", "action": {"tool": "invoke_capability", '
        '"args": {"function": "TollStation.congestion_report", '
        '"target": "TollStation/ST001", "args": {}}}}',
        '{"think": "证据齐了", "answer": "济南西收费站处于拥堵状态：'
        '近1小时通行3辆车，系统已生成拥堵报告。"}',
    ]
    run = platform.intelligence.agent(wid, "analyst", "济南西收费站为什么拥堵？",
                                      provider=EchoLLMProvider(script))
    print(f"\n[Agent] ok={run['ok']}  步数={len(run['steps'])}")
    for step in run["steps"]:
        print(f"  step{step['index']}: {step['thought']}  ->  {step['action'].get('tool')}")
    print(f"  答案: {run['answer']}")

    # ---------------------------------------------------------- 7 governance
    banner("Step 6 · 治理：访问 / 生命周期 / 审计（design.txt §29）")
    try:
        platform.capabilities.invoke(wid, "mallory", "Vehicle.recharge",
                                     target="Vehicle/V001", args={"amount": 1})
    except Exception as exc:
        print(f"外部用户 mallory 调用能力 -> 拒绝: {type(exc).__name__}")
    try:
        platform.ontologies.upsert_object(wid, "admin", "Alien",
                                          properties=[{"name": "x", "type": "string"}])
    except Exception as exc:
        print(f"修改已发布本体 -> 拒绝: {exc}")
    changes = platform.ctx.gateways.for_workspace(wid, {}).observe_changes()
    print(f"Kernel 变更流: {len(changes)} 条（source 分布: ", end="")
    sources: dict[str, int] = {}
    for change in changes:
        sources[change["source"]] = sources.get(change["source"], 0) + 1
    print(json.dumps(sources, ensure_ascii=False) + "）")
    audit = platform.ctx.audit.list(wid, limit=8)
    print(f"审计日志最近 {len(audit)} 条: {[e['action'] for e in audit]}")

    banner("完成 · 同一个世界可以用 CLI 继续 查看/调用")
    print(f"  onto --db {demo_db} state find TollStation --filter status=congested")
    print(f"  onto --db {demo_db} ask --workspace {wid} \"济南西收费站为什么拥堵？\"")


if __name__ == "__main__":
    main()
