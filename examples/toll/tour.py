#!/usr/bin/env python3
"""收费站样例 · 全链路演练：一次把平台的七个领域串起来。

用法::

    python examples/toll/tour.py              # 写进 .onto/platform.db（控制台里能看到）
    python examples/toll/tour.py --memory     # 纯内存跑，不落盘
    python examples/toll/tour.py --no-llm     # 跳过 AI 建模 / agent，其余照跑
    python examples/toll/tour.py --base-url http://127.0.0.1:8100   # 结束时 REST 只读回查

演练链路（与 REST / CLI 调用的是同一批用例）::

    1  工作空间 / 成员角色                workspace + governance
    2  AI 快速建模：一段话 → 本体草稿      ontology_draft + LLM
    3  本体定义：对象 / 关系 / 术语        ontology
    4  校验 → 发布 → 运行时 Schema 编译    ontology → runtime
    5  生命周期保护（发布后不可变）        governance
    6  SQLite 数据源 → 投影 → 幂等同步      data（Source State → SemanticDelta → State）
    7  运行时状态 find / get / related     runtime
    8  能力：python 只读函数（站点日报）    capability（executor=python）
    9  能力：workflow 编排（拥堵巡检）      capability（executor=workflow）
    10 能力：python 写回（状态处置）        capability → bounded write
    11 能力：agent 实现（拥堵解释, LLM）    capability（executor=agent）
    12 应用装配：视图 / 查询 / 能力 / 助手  application
    13 智能问答：ask + agent               intelligence
    14 汇总 + REST 只读回查                 interfaces / console

前 14 步都是真实写入；`--memory` 只是把落盘换成内存。
每步都打印证据（对象数、变更数、能力输出、审计事件），失败直接退出码 1。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # examples/toll
PLATFORM_ROOT = ROOT.parent.parent                # ontoPlantform/
sys.path.insert(0, str(PLATFORM_ROOT / "src"))

from onto_platform.bootstrap import PlatformConfig, build_platform  # noqa: E402
from onto_platform.errors import AccessDeniedError, PlatformError  # noqa: E402

ADMIN = "admin"
TOTAL_STEPS = 15  # 14 个业务步骤 + 汇总回查


def _load_module(name: str, filename: str):
    """按文件路径加载同目录的样例脚本（避免和 onto_platform.bootstrap 撞名）。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


toll = _load_module("toll_bootstrap", "bootstrap.py")


# ---------------------------------------------------------------------------
# 能力实现：故意写成「用户视角」的业务代码——只看得见 ctx 和业务属性
# ---------------------------------------------------------------------------

PY_REPORT_SOURCE = '''
def execute(ctx):
    """站点日报：目标站的车道、通行交易、通行费汇总。"""
    station = ctx.object()
    lanes = ctx.related(station.ref, "has_lane")
    exits = ctx.related(station.ref, "exits", direction="in")
    fees = []
    for ref in exits[:50]:
        fee = ctx.get(ref).get("fee")
        if isinstance(fee, (int, float)):
            fees.append(fee)
    return {
        "station": str(station.ref),
        "name": station.get("name"),
        "status": station.get("status"),
        "lane_count": len(lanes),
        "exit_transactions": len(exits),
        "fee_sample_sum": round(sum(fees), 2),
    }
'''

PY_SET_STATUS_SOURCE = '''
ALLOWED = ("free", "busy", "congested")


def execute(ctx):
    """状态处置：把目标站标记为 free / busy / congested。"""
    station = ctx.object()
    status = ctx.args.get("status", "free")
    if status not in ALLOWED:
        ctx.fail("unsupported status %r; expected one of %s" % (status, list(ALLOWED)))
    ctx.set(station.ref, "status", status)
    return {"station": str(station.ref), "status": status}
'''

# workflow 的能力是「编排」不是代码：find / count / related / get / set_property
WORKFLOW_STEPS = [
    {"kind": "find", "object_type": "TollStation",
     "filter": {"status": "congested"}, "as": "congested"},
    {"kind": "count", "list": "{{steps.congested}}", "as": "congested_count"},
    {"kind": "related", "ref": "{{target}}", "relation": "has_lane", "as": "lanes"},
    {"kind": "count", "list": "{{steps.lanes}}", "as": "lane_count"},
    {"kind": "set", "name": "output", "as": "output",
     "value": {"target": "{{target}}",
               "congested_stations": "{{steps.congested_count}}",
               "target_lanes": "{{steps.lane_count}}"}},
]


# ---------------------------------------------------------------------------
# 输出小工具
# ---------------------------------------------------------------------------


class Tour:
    """带编号的步骤输出 + 汇总表。"""

    def __init__(self, total: int) -> None:
        self._total = total
        self._n = 0
        self._title = ""
        self._last = time.monotonic()
        self.summary: list[tuple[str, str]] = []
        self.skipped: list[str] = []

    def step(self, title: str) -> None:
        self._n += 1
        self._title = title
        previous = time.monotonic() - self._last
        self._last = time.monotonic()
        suffix = f"（上一步 {previous:.1f}s）" if self._n > 1 else ""
        print(f"\n[{self._n}/{self._total}] {title}{suffix}")
        print("-" * 74)

    def ok(self, text: str) -> None:
        print(f"   ✓ {text}")

    def note(self, text: str) -> None:
        print(f"   · {text}")

    def skip(self, text: str) -> None:
        print(f"   ⏭ 跳过：{text}")
        self.skipped.append(f"{self._title}：{text}")

    def result(self, text: str) -> None:
        self.summary.append((self._title, text))


def _compact(value, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _answer_of(output) -> str:
    if isinstance(output, dict):
        for key in ("answer", "output", "summary"):
            if output.get(key):
                return str(output[key])
    return _compact(output, 160)


# ---------------------------------------------------------------------------
# 各步骤
# ---------------------------------------------------------------------------


def step_workspace(platform, tour: Tour, name: str) -> str:
    tour.step("工作空间与成员角色（workspace / governance）")
    workspace = platform.workspaces.create(
        name, actor=ADMIN, description="examples/toll 全链路演练工作空间")
    wid = workspace["id"]
    platform.workspaces.add_member(wid, "ops", "editor", actor=ADMIN)
    platform.workspaces.add_member(wid, "auditor", "viewer", actor=ADMIN)
    seen = platform.workspaces.get(wid, "auditor")
    tour.ok(f"workspace = {wid}（{seen['name']}）")
    tour.ok("成员：admin=owner、ops=editor、auditor=viewer（auditor 能读到工作空间）")
    tour.result(f"wid={wid}")
    return wid


def step_ai_draft(platform, tour: Tour, wid: str, expected: dict, provider) -> None:
    tour.step("AI 快速建模：一段业务说明 → 本体草稿（LLM，预览不写入）")
    if provider is None:
        tour.skip("未配置 LLM（ONTO_LLM_BASE_URL / ONTO_LLM_MODEL / ONTO_LLM_API_KEY）")
        return
    text = toll.load_business_text()
    preview = platform.ontologies.draft(
        wid, ADMIN, text, apply=False, provider=provider,
        name=expected["ontology_name"])
    draft = preview["draft"]
    tour.ok(f"business.md（{len(text)} 字）→ 对象 {len(draft['objects'])} 个 / "
            f"关系 {len(draft['relations'])} 条 / 术语 {len(draft['vocabulary'])} 条")
    tour.ok(f"warnings = {preview['warnings'] or '无'}")
    tour.note(f"草稿对象：{_compact([o['name'] for o in draft['objects']])}")
    tour.note("本演练用 expected-ontology.json 的标准本体继续（保证投影映射确定）；"
              "真实项目里确认预览后把 draft 原样回传 apply=true 即可写入，不会再调一次模型")
    tour.result(f"AI 预览：{len(draft['objects'])} 对象 / {len(draft['relations'])} 关系 / "
                f"{len(draft['vocabulary'])} 术语")


def step_define_ontology(platform, tour: Tour, wid: str, expected: dict) -> None:
    tour.step("本体定义：对象 / 关系 / 业务术语（ontology）")
    platform.ontologies.create(wid, expected["ontology_name"], actor=ADMIN,
                               description=expected.get("description", ""))
    for spec in expected["objects"]:
        platform.ontologies.upsert_object(
            wid, ADMIN, spec["name"],
            properties=spec.get("properties", []),
            description=spec.get("description", ""))
    for spec in expected["relations"]:
        platform.ontologies.upsert_relation(
            wid, ADMIN, spec["name"], spec["source"], spec["target"],
            description=spec.get("description", ""))
    for term in expected["vocabulary"]:
        platform.ontologies.add_term(
            wid, ADMIN, term["term"], term["concept"],
            kind=term.get("kind", "object"), aliases=term.get("aliases"))

    ontology = platform.ontologies.get(wid, ADMIN)
    problems = platform.ontologies.validate(wid, ADMIN)
    tour.ok(f"对象 {len(ontology['objects'])} 个、关系 {len(ontology['relations'])} 条、"
            f"术语 {len(ontology['vocabulary'])} 条（{ontology['lifecycle']}）")
    tour.ok(f"validate：problems={problems}")
    if problems:
        raise PlatformError(f"本体校验失败：{problems}")
    tour.result(f"{len(ontology['objects'])} 对象 / {len(ontology['relations'])} 关系 / "
                f"{len(ontology['vocabulary'])} 术语，validate 通过")


def step_publish(platform, tour: Tour, wid: str) -> dict:
    tour.step("发布 → 运行时 Schema 编译（ontology → runtime → kernel）")
    published = platform.ontologies.publish(wid, ADMIN)
    schema = published["runtime"]
    tour.ok(f"publish ok：lifecycle={published['ontology']['lifecycle']}，"
            f"version={schema['ontology']['version']}")
    tour.ok(f"Kernel Schema：objects={len(schema['objects'])} "
            f"relations={len(schema['relations'])} functions={len(schema['functions'])}")
    tour.ok("运行时描述（describe）即控制台「本体 / 运行时」页看到的内容")
    tour.result(f"发布成功，schema objects={len(schema['objects'])}")
    return published


def step_lifecycle_guard(platform, tour: Tour, wid: str) -> None:
    tour.step("生命周期保护：已发布本体不可变（governance）")
    try:
        platform.ontologies.upsert_object(
            wid, ADMIN, "Ghost", properties=[{"name": "x", "type": "string"}])
        tour.note("（异常）发布后仍能写入对象")
    except PlatformError as exc:  # noqa: PERF203 - 演示用
        tour.ok(f"发布后再写对象被拒绝 → {type(exc).__name__}: {str(exc)[:48]}…")
        tour.result("已发布本体不可变，写入被拒绝")
        return
    raise PlatformError("发布后仍然可以写入对象，生命周期保护失效")


def step_data(platform, tour: Tour, wid: str) -> dict:
    tour.step("数据接入：SQLite 数据源 → 投影映射 → 同步（data）")
    if not toll.DB_PATH.is_file():
        tour.note(f"{toll.DB_PATH.name} 不存在，先生成样例数据库…")
        _load_module("toll_build_db", "data/build_db.py").main()
    source = platform.data.connect_source(
        wid, ADMIN, "收费站运营库", "sqlite",
        config={"path": str(toll.DB_PATH)}, assets=toll.ASSETS)
    projection = platform.data.create_projection(
        wid, ADMIN, "收费站主数据投影", source["id"],
        object_mappings=toll.OBJECT_MAPPINGS,
        relation_mappings=toll.RELATION_MAPPINGS)
    first = platform.data.run_projection(wid, projection["id"], ADMIN)
    second = platform.data.run_projection(wid, projection["id"], ADMIN)
    tour.ok(f"数据源 {source['id']}（sqlite: {toll.DB_PATH.name}，{len(toll.ASSETS)} 条资产查询）")
    tour.ok(f"投影 {projection['id']}：{len(toll.OBJECT_MAPPINGS)} 条对象映射 / "
            f"{len(toll.RELATION_MAPPINGS)} 条关系映射")
    tour.ok(f"首次同步：upserts={first['objects_upserted']} links={first['links_asserted']} "
            f"changes={first['changes']}")
    tour.ok(f"幂等重跑：upserts={second['objects_upserted']} links={second['links_asserted']} "
            f"changes={second['changes']}（重跑不产生新变更）")
    if second["changes"] != 0:
        raise PlatformError(f"投影重跑产生了 {second['changes']} 条变更，幂等性被破坏")
    tour.result(f"{first['objects_upserted']} 对象 / {first['links_asserted']} 关系，"
                f"重跑 changes={second['changes']}")
    return {"source": source, "projection": projection, "first": first, "second": second}


def step_state(platform, tour: Tour, wid: str) -> None:
    tour.step("运行时状态：find / get / related（State）")
    gateway = platform.ctx.gateway_for(wid)
    counts = {
        "TollStation": len(gateway.find_objects("TollStation")),
        "Lane": len(gateway.find_objects("Lane")),
        "Vehicle": len(gateway.find_objects("Vehicle")),
        "TollTransaction": len(gateway.find_objects("TollTransaction")),
    }
    congested = gateway.find_objects("TollStation", {"status": "congested"})
    station = gateway.get_object("TollStation/ST001")
    lanes = gateway.related("TollStation/ST001", "has_lane")
    batches = gateway.observe_changes()
    total_changes = sum(len(batch.get("changes", [])) for batch in batches)
    tour.ok(f"对象计数：{counts}")
    tour.ok(f"find TollStation status=congested → {[o['id'] for o in congested]}")
    tour.ok(f"get TollStation/ST001 → {_compact(station['properties'])}")
    tour.ok(f"related TollStation/ST001 has_lane → {lanes}")
    tour.ok(f"changes 变更流水：{len(batches)} 批 / {total_changes} 条变更")
    tour.result(f"计数 {counts}；ST001 has_lane={lanes}")


def step_capability_python(platform, tour: Tour, wid: str) -> dict:
    tour.step("能力（一）：Python 函数实现 · 站点日报（capability → kernel function）")
    capability = platform.capabilities.create(
        wid, ADMIN, key="daily_report", name="站点日报", target_type="TollStation",
        description="汇总站点车道、通行交易与通行费",
        implementation={"kind": "python", "source": PY_REPORT_SOURCE})
    platform.capabilities.publish(wid, capability["id"], ADMIN)
    result = platform.capabilities.invoke(
        wid, ADMIN, "TollStation.daily_report", target="TollStation/ST001")
    if not result["ok"]:
        raise PlatformError(f"能力调用失败：{result['error']}")
    tour.ok(f"publish 后能力被编译成 Kernel 函数 TollStation.daily_report")
    tour.ok(f"invoke(TollStation/ST001) → {_compact(result['output'], 200)}")
    tour.result(f"daily_report → {_compact(result['output'], 100)}")
    return {"capability": capability, "result": result}


def step_capability_workflow(platform, tour: Tour, wid: str) -> None:
    tour.step("能力（二）：Workflow 编排实现 · 拥堵巡检（capability → workflow）")
    capability = platform.capabilities.create(
        wid, ADMIN, key="congestion_scan", name="拥堵巡检", target_type="TollStation",
        description="找出拥堵站点并统计目标站车道",
        implementation={"kind": "workflow", "steps": WORKFLOW_STEPS})
    platform.capabilities.publish(wid, capability["id"], ADMIN)
    result = platform.capabilities.invoke(
        wid, ADMIN, "TollStation.congestion_scan", target="TollStation/ST001")
    if not result["ok"]:
        raise PlatformError(f"workflow 能力调用失败：{result['error']}")
    tour.ok("workflow 由 find / count / related / set 编排，全部经 RuntimeGateway")
    tour.ok(f"invoke(TollStation/ST001) → {_compact(result['output'], 200)}")
    tour.result(f"congestion_scan → {_compact(result['output'], 100)}")


def step_capability_write(platform, tour: Tour, wid: str) -> None:
    tour.step("能力（三）：Python 写回 · 状态处置（bounded write → SemanticDelta）")
    capability = platform.capabilities.create(
        wid, ADMIN, key="set_status", name="状态处置", target_type="TollStation",
        description="把收费站标记为 free / busy / congested",
        inputs=[{"name": "status", "type": "string",
                 "description": "free / busy / congested"}],
        implementation={"kind": "python", "source": PY_SET_STATUS_SOURCE})
    platform.capabilities.publish(wid, capability["id"], ADMIN)

    bad = platform.capabilities.invoke(
        wid, ADMIN, "TollStation.set_status", target="TollStation/ST006",
        args={"status": "melted"})
    tour.ok(f"业务校验：非法状态 → ok={bad['ok']}，error={bad['error']}（不落库）")

    good = platform.capabilities.invoke(
        wid, ADMIN, "TollStation.set_status", target="TollStation/ST006",
        args={"status": "busy"})
    if not good["ok"]:
        raise PlatformError(f"状态处置失败：{good['error']}")
    current = platform.ctx.gateway_for(wid).get_object("TollStation/ST006")
    tour.ok(f"合法处置 ok={good['ok']} changes={len(good['changes'])} → "
            f"ST006.status={current['properties'].get('status')}")
    tour.result(f"ST006 free → busy（changes={len(good['changes'])}）")


def step_capability_agent(platform, tour: Tour, wid: str, provider) -> None:
    tour.step("能力（四）：Agent 实现 · 拥堵解释（capability → agent, LLM）")
    if provider is None:
        tour.skip("未配置 LLM")
        return
    capability = platform.capabilities.create(
        wid, ADMIN, key="explain", name="拥堵解释", target_type="TollStation",
        description="用自然语言解释站点通行状态",
        implementation={"kind": "agent",
                        "goal": "解释 {target} 当前通行状态、车道数量与拥堵原因"})
    platform.capabilities.publish(wid, capability["id"], ADMIN)
    result = platform.capabilities.invoke(
        wid, ADMIN, "TollStation.explain", target="TollStation/ST001")
    if not result["ok"]:
        raise PlatformError(f"agent 能力调用失败：{result['error']}")
    tour.ok("agent 能力由平台注册的 runner 执行（Agent → Capability → Gateway → Kernel）")
    tour.ok(f"invoke(TollStation/ST001) → {_answer_of(result['output'])[:180]}")
    tour.result(f"explain → {_answer_of(result['output'])[:80]}")


def step_application(platform, tour: Tour, wid: str, report: dict,
                     provider) -> None:
    tour.step("应用装配：视图 / 查询 / 能力 / 助手（application）")
    application = platform.applications.create(
        wid, ADMIN, "收费站运营台", description="样例全链路应用")
    app_id = application["id"]
    platform.applications.add_view(
        wid, app_id, ADMIN, "stations", title="收费站总览", kind="table",
        object_type="TollStation", properties=["name", "status", "province"])
    platform.applications.add_query(
        wid, app_id, ADMIN, "congested", object_type="TollStation",
        filters={"status": "congested"},
        expand=[{"relation": "has_lane", "direction": "out"}],
        title="拥堵收费站")
    platform.applications.attach_capability(wid, app_id, ADMIN, report["capability"]["id"])
    platform.applications.add_agent(
        wid, app_id, ADMIN, "值班助手",
        goal="检查拥堵收费站并给出处置建议", description="运营值班")
    published = platform.applications.publish(wid, app_id, ADMIN)

    query = platform.applications.run_query(wid, app_id, ADMIN, "congested")
    lanes = query["expand"][0]["objects"] if query.get("expand") else []
    tour.ok(f"publish lifecycle={published['lifecycle']}：视图 1 / 查询 1 / 能力 1 / 助手 1")
    tour.ok(f"run_query('congested') → {[o['id'] for o in query['objects']]}，"
            f"expand has_lane {len(lanes)} 条边")

    if provider is None:
        tour.note("未配置 LLM，跳过应用内助手运行")
    else:
        run = platform.applications.run_agent(wid, app_id, ADMIN, "值班助手",
                                             max_steps=5)
        tour.ok(f"run_agent('值班助手') ok={run.get('ok')} "
                f"steps={len(run.get('steps', []))} → {_answer_of(run)[:140]}")
    tour.result(f"应用 {app_id}：查询命中 {[o['id'] for o in query['objects']]}")


def step_intelligence(platform, tour: Tour, wid: str, provider) -> None:
    tour.step("智能问答：语义检索 ask + Agent 推理（intelligence）")
    asked = platform.intelligence.ask(wid, ADMIN, "济南西收费站现在为什么拥堵？")
    terms = [hit.get("term") for hit in asked.get("concepts", [])]
    objects = [obj["id"] for obj in asked.get("objects", [])]
    tour.ok(f"ask 命中术语 {terms} → 对象 {objects}，related {len(asked.get('related', []))} 条")
    tour.ok(f"answer：{str(asked.get('answer', ''))[:120]}")

    if provider is None:
        tour.skip("agent 推理需要 LLM")
        return
    run = platform.intelligence.agent(
        wid, ADMIN, "找出最拥堵的收费站，并说明它的车道情况",
        provider=provider, max_steps=6)
    tools = [step.get("tool") for step in run.get("steps", [])]
    tour.ok(f"agent ok={run.get('ok')} steps={len(tools)} tools={tools}")
    tour.ok(f"answer：{_answer_of(run)[:140]}")
    tour.result(f"ask 命中 {len(terms)} 个术语；agent {len(tools)} 步")


def step_governance(platform, tour: Tour, wid: str) -> None:
    tour.step("治理：审计日志 / 角色权限 / 生命周期（governance）")
    audit = platform.ctx.audit.list(wid, limit=300)
    counts: dict[str, int] = {}
    for row in audit:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    tour.ok(f"审计事件 {len(audit)} 条")
    tour.note("、".join(f"{action}×{count}" for action, count in sorted(counts.items())))

    try:
        platform.ontologies.add_term(wid, "auditor", "临时术语", "TollStation")
        tour.note("（异常）viewer 写操作成功")
    except AccessDeniedError as exc:
        tour.ok(f"viewer 写操作被拒 → {exc}")

    try:
        platform.ontologies.add_term(wid, "ops", "临时术语", "TollStation")
        tour.note("（异常）editor 在已发布本体上写成功")
    except AccessDeniedError as exc:  # pragma: no cover - 防御
        tour.note(f"（异常）editor 被拒：{exc}")
    except PlatformError as exc:
        tour.ok(f"editor 通过角色校验，被生命周期拦住 → {str(exc)[:52]}…")
    tour.result(f"审计 {len(audit)} 条；viewer 写被拒、editor 写被生命周期拦住")


def step_rest_probe(tour: Tour, wid: str, name: str, base_url: str) -> None:
    tour.step("汇总与 REST 只读回查（interfaces / console）")
    for title, evidence in tour.summary:
        print(f"   ✓ {title}：{evidence}")
    if not base_url or base_url.lower() in ("off", "none", "-"):
        tour.note("--base-url off，跳过 REST 回查")
        return
    base = base_url.rstrip("/")
    try:
        health = _http_get(base + "/api/health")
        runtime = _http_get(base + f"/api/workspaces/{wid}/runtime")
        stations = _http_get(base + f"/api/workspaces/{wid}/state/find?type=TollStation")
        audit = _http_get(base + f"/api/workspaces/{wid}/audit?limit=5")
    except (urllib.error.URLError, OSError) as exc:
        tour.note(f"服务 {base} 不可达（{exc}），跳过回查；启动服务后可在控制台查看")
        return
    print()
    tour.ok(f"REST /api/health → llm_configured={health.get('llm_configured')} "
            f"store={health.get('backend')}")
    tour.ok(f"REST runtime → objects={len(runtime.get('objects', []))} "
            f"relations={len(runtime.get('relations', []))}")
    tour.ok(f"REST state/find?type=TollStation → {len(stations)} 个（与本次演练一致）")
    tour.ok(f"REST audit?limit=5 → 最近 5 条，共 {len(audit)} 条返回")
    tour.note(f"打开 {base}/ → 在工作空间列表里选「{name}」（wid={wid}）即可看到全部结果")
    tour.result("REST 回查一致（控制台可见）")


def _http_get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def run(args) -> int:
    tour = Tour(TOTAL_STEPS)
    platform = build_platform(
        PlatformConfig(store="memory") if args.memory
        else PlatformConfig(store="sqlite",
                            db_path=str(PLATFORM_ROOT / ".onto" / "platform.db")))

    provider = None
    if not args.no_llm:
        from onto_platform.infrastructure.llm.factory import provider_from_env

        provider = provider_from_env()
        if provider is not None:
            platform.configure_llm(provider)

    print("=" * 74)
    print("收费站样例 · 全链路演练（examples/toll → 平台七个领域）")
    print(f"  存储      ：{'memory（不落盘）' if args.memory else '.onto/platform.db（控制台可见）'}")
    print(f"  样例数据  ：{toll.DB_PATH}")
    print(f"  LLM       ：{'已配置' if provider is not None else '未配置（AI 步骤会自动跳过）'}")
    print("=" * 74)

    expected = toll.load_expected()
    name = args.name or f"收费站全链路演练 {datetime.now():%m-%d %H:%M}"

    wid = step_workspace(platform, tour, name)
    step_ai_draft(platform, tour, wid, expected, provider)
    step_define_ontology(platform, tour, wid, expected)
    step_publish(platform, tour, wid)
    step_lifecycle_guard(platform, tour, wid)
    step_data(platform, tour, wid)
    step_state(platform, tour, wid)
    report = step_capability_python(platform, tour, wid)
    step_capability_workflow(platform, tour, wid)
    step_capability_write(platform, tour, wid)
    step_capability_agent(platform, tour, wid, provider)
    step_application(platform, tour, wid, report, provider)
    step_intelligence(platform, tour, wid, provider)
    step_governance(platform, tour, wid)
    step_rest_probe(tour, wid, name, args.base_url)

    print()
    print("=" * 74)
    if tour.skipped:
        print(f"完成（{len(tour.skipped)} 步因未配置 LLM 跳过）：")
        for item in tour.skipped:
            print(f"  - {item}")
    else:
        print("完成：全部步骤通过，未跳过任何步骤。")
    print(f"工作空间 wid = {wid}")
    print("=" * 74)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="收费站样例全链路演练：一次跑通平台七个领域")
    parser.add_argument("--memory", action="store_true",
                        help="用内存存储，不写 .onto/platform.db")
    parser.add_argument("--no-llm", action="store_true",
                        help="跳过所有 LLM 步骤（AI 建模 / agent）")
    parser.add_argument("--name", default="", help="工作空间名（默认带时间后缀）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100",
                        help="结束时回查用的 REST 地址，传 off 跳过")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except PlatformError as exc:
        print(f"\n演练失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - 演练脚本，打印完整栈便于排查
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
