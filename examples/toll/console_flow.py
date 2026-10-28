#!/usr/bin/env python3
"""控制台工作台 STEP 3–5 的 HTTP 验证（与 GUI 走同一条 API 路由）。

`bootstrap.py` 完成 STEP 3–4（发布 + 数据进来），`tour.py` 用进程内平台对象
跑全链路；本脚本补上**控制台视角**：所有调用都走 `http://127.0.0.1:8100`
的 REST 路由，正是前端页面按钮发出的那些请求。

    python console_flow.py                     # 新建工作空间并跑完 STEP 3–5
    python console_flow.py --name 我的演示      # 指定工作空间名
    python console_flow.py --base-url http://127.0.0.1:8100
    python console_flow.py --no-agent          # 跳过两个 LLM Agent 入口

覆盖：发布业务世界 → 接数据（投影幂等）→ 四种能力（python / workflow /
写回 / agent）→ 组装应用（视图/查询/能力/助手，发布后查询与助手）→
智能问答 ask + 通用 Agent → 治理（成员角色、越权拦截、审计）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "http://127.0.0.1:8100"


def _load_sample_bootstrap():
    """按文件路径加载同目录的 bootstrap.py（避免与 onto_platform.bootstrap 撞名）。"""
    spec = importlib.util.spec_from_file_location("toll_bootstrap", ROOT / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load_sample_bootstrap()


# ---------------------------------------------------------------------------
# 极简 HTTP 客户端：失败时抛出带平台 error 详情的异常
# ---------------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None):
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(self.base_url + path, data=data,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail[:400]}") from None
        return json.loads(raw) if raw else None

    # -- workspace / ontology / data（STEP 3–4）--------------------------
    def workspace(self, wid: str) -> dict:
        return self._request("GET", f"/api/workspaces/{wid}")

    def runtime(self, wid: str) -> dict:
        return self._request("GET", f"/api/workspaces/{wid}/runtime")

    def find(self, wid: str, object_type: str, **filters) -> list[dict]:
        query = urllib.parse.urlencode({"type": object_type, **filters})
        return self._request("GET", f"/api/workspaces/{wid}/state/find?{query}")

    def get_object(self, wid: str, ref: str) -> dict:
        query = urllib.parse.urlencode({"ref": ref})
        return self._request("GET", f"/api/workspaces/{wid}/state/get?{query}")

    def related(self, wid: str, ref: str, relation: str) -> list[str]:
        query = urllib.parse.urlencode({"ref": ref, "relation": relation,
                                        "direction": "out"})
        return self._request("GET", f"/api/workspaces/{wid}/state/related?{query}")

    def changes(self, wid: str) -> list[dict]:
        return self._request("GET", f"/api/workspaces/{wid}/changes?after=0")

    # -- capability（STEP 5a）-------------------------------------------
    def create_capability(self, wid: str, body: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/capabilities", body)

    def publish_capability(self, wid: str, capability_id: str) -> dict:
        return self._request(
            "POST", f"/api/workspaces/{wid}/capabilities/{capability_id}/publish")

    def invoke(self, wid: str, function: str, target: str | None = None,
               args: dict | None = None, user: str | None = None) -> dict:
        body: dict = {"function": function}
        if target is not None:
            body["target"] = target
        if args is not None:
            body["args"] = args
        if user is not None:
            body["user"] = user
        return self._request("POST", f"/api/workspaces/{wid}/invoke", body)

    # -- application（STEP 5b）------------------------------------------
    def create_application(self, wid: str, name: str, description: str) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/applications",
                             {"name": name, "description": description})

    def add_view(self, wid: str, app_id: str, body: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/applications/{app_id}/views",
                             body)

    def add_query(self, wid: str, app_id: str, body: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/applications/{app_id}/queries",
                             body)

    def attach_capability(self, wid: str, app_id: str, capability_id: str) -> dict:
        return self._request(
            "POST", f"/api/workspaces/{wid}/applications/{app_id}/capabilities",
            {"capability_id": capability_id})

    def add_agent(self, wid: str, app_id: str, body: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/applications/{app_id}/agents",
                             body)

    def publish_application(self, wid: str, app_id: str) -> dict:
        return self._request(
            "POST", f"/api/workspaces/{wid}/applications/{app_id}/publish")

    def run_query(self, wid: str, app_id: str, alias: str) -> dict:
        return self._request(
            "POST", f"/api/workspaces/{wid}/applications/{app_id}/query",
            {"alias": alias})

    def run_app_agent(self, wid: str, app_id: str, alias: str, max_steps: int) -> dict:
        quoted = urllib.parse.quote(alias)
        return self._request(
            "POST",
            f"/api/workspaces/{wid}/applications/{app_id}/agents/{quoted}/agent",
            {"max_steps": max_steps})

    # -- intelligence（STEP 5c）-----------------------------------------
    def ask(self, wid: str, question: str) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/ask",
                             {"question": question})

    def agent(self, wid: str, goal: str, max_steps: int) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/agent",
                             {"goal": goal, "max_steps": max_steps})

    # -- governance（STEP 5d）-------------------------------------------
    def add_member(self, wid: str, member: str, role: str) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/members",
                             {"member": member, "role": role})

    def audit(self, wid: str, limit: int = 300) -> list[dict]:
        return self._request("GET", f"/api/workspaces/{wid}/audit?limit={limit}")


# ---------------------------------------------------------------------------
# 输出小工具
# ---------------------------------------------------------------------------


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def ok(text: str) -> None:
    print(f"  ✓ {text}")


def note(text: str) -> None:
    print(f"  · {text}")


def compact(value, limit: int = 200) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# 能力实现（与 tour.py 相同的业务代码，改名以适应标准本体）
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--name", default="收费站控制台演练")
    parser.add_argument("--no-agent", action="store_true",
                        help="跳过 agent 能力与两个 Agent 入口（不调 LLM）")
    args = parser.parse_args(argv)
    started = time.monotonic()

    api = Api(args.base_url)

    section("STEP 3–4 · 发布业务世界 + 接入现实数据（bootstrap.py 的 HTTP 流程）")
    expected = bootstrap.load_expected()
    expected["workspace_name"] = args.name
    report = bootstrap.bootstrap(
        bootstrap.HttpBackend(args.base_url),
        expected,
    )
    wid = report["wid"]
    note(f"工作空间 wid = {wid}（控制台刷新后可见）")

    section("STEP 4 回查 · Runtime Schema / 状态计数 / 投影幂等")
    runtime = api.runtime(wid)
    ok(f"Kernel Schema：objects={len(runtime.get('objects', {}))} "
       f"relations={len(runtime.get('relations', {}))} "
       f"functions={len(runtime.get('functions', []))}")
    schema_objects = runtime.get("objects") or {}
    object_names = [o["name"] if isinstance(o, dict) else o
                    for o in (schema_objects if isinstance(schema_objects, list)
                              else schema_objects.values())]
    counts = {name: len(api.find(wid, name)) for name in object_names}
    ok(f"对象计数：{counts}")
    congested = api.find(wid, "TollStation", status="congested")
    ok(f"find TollStation status=congested → {[o['id'] for o in congested]}")
    ok(f"get TollStation/ST001 → {compact(api.get_object(wid, 'TollStation/ST001')['properties'])}")
    ok(f"related TollStation/ST001 has_lane → {api.related(wid, 'TollStation/ST001', 'has_lane')}")
    rerun = api._request(
        "POST", f"/api/workspaces/{wid}/projections/{report['projection_id']}/run")
    ok(f"投影幂等重跑：objects_upserted={rerun['objects_upserted']} "
       f"links_asserted={rerun['links_asserted']} changes={rerun['changes']}")
    batches = api.changes(wid)
    ok(f"变更流水：{len(batches)} 批 / {sum(len(b['changes']) for b in batches)} 条")
    assert rerun["changes"] == 0, "projection rerun is not idempotent"

    section("STEP 5a · 四种能力（python / workflow / 写回 / agent）")
    daily = api.create_capability(wid, {
        "key": "daily_report", "name": "站点日报", "target_type": "TollStation",
        "description": "汇总站点车道、通行交易与通行费",
        "implementation": {"kind": "python", "source": PY_REPORT_SOURCE}})
    api.publish_capability(wid, daily["id"])
    result = api.invoke(wid, "TollStation.daily_report", "TollStation/ST001")
    ok(f"daily_report(ST001) → {compact(result['output'])}")

    scan = api.create_capability(wid, {
        "key": "congestion_scan", "name": "拥堵巡检", "target_type": "TollStation",
        "description": "找出拥堵站点并统计目标站车道",
        "implementation": {"kind": "workflow", "steps": WORKFLOW_STEPS}})
    api.publish_capability(wid, scan["id"])
    result = api.invoke(wid, "TollStation.congestion_scan", "TollStation/ST001")
    ok(f"congestion_scan(ST001) → {compact(result['output'])}")

    setter = api.create_capability(wid, {
        "key": "set_status", "name": "状态处置", "target_type": "TollStation",
        "description": "把收费站标记为 free / busy / congested",
        "inputs": [{"name": "status", "type": "string",
                    "description": "free / busy / congested"}],
        "implementation": {"kind": "python", "source": PY_SET_STATUS_SOURCE}})
    api.publish_capability(wid, setter["id"])
    bad = api.invoke(wid, "TollStation.set_status", "TollStation/ST006",
                     args={"status": "melted"})
    ok(f"非法状态：ok={bad['ok']} error={bad['error']}（不落库）")
    good = api.invoke(wid, "TollStation.set_status", "TollStation/ST006",
                      args={"status": "busy"})
    current = api.get_object(wid, "TollStation/ST006")
    ok(f"合法写回：ok={good['ok']} changes={len(good['changes'])} → "
       f"ST006.status={current['properties'].get('status')}")

    if args.no_agent:
        note("--no-agent，跳过 agent 能力与 Agent 入口")
    else:
        explain = api.create_capability(wid, {
            "key": "explain", "name": "拥堵解释", "target_type": "TollStation",
            "description": "用自然语言解释站点通行状态",
            "implementation": {"kind": "agent",
                               "goal": "解释 {target} 当前通行状态、车道数量与拥堵原因"}})
        api.publish_capability(wid, explain["id"])
        result = api.invoke(wid, "TollStation.explain", "TollStation/ST001")
        output = result["output"] or {}
        ok(f"explain(ST001) ok={result['ok']} agent_ok={output.get('ok')} "
           f"steps={output.get('steps')}")
        ok(f"answer：{str(output.get('answer', ''))[:160]}")

    section("STEP 5b · 组装应用（视图 / 查询 / 能力 / 助手 → publish → 运行）")
    application = api.create_application(wid, "收费站运营台", "样例全链路应用")
    app_id = application["id"]
    api.add_view(wid, app_id, {
        "alias": "stations", "title": "收费站总览", "kind": "table",
        "object_type": "TollStation", "properties": ["name", "status", "province"]})
    api.add_query(wid, app_id, {
        "alias": "congested", "object_type": "TollStation", "title": "拥堵收费站",
        "filters": {"status": "congested"},
        "expand": [{"relation": "has_lane", "direction": "out"}]})
    api.attach_capability(wid, app_id, daily["id"])
    api.add_agent(wid, app_id, {
        "alias": "值班助手", "goal": "检查拥堵收费站并给出处置建议",
        "description": "运营值班"})
    published = api.publish_application(wid, app_id)
    ok(f"应用 {app_id} 发布：lifecycle={published['lifecycle']}，"
       f"视图 1 / 查询 1 / 能力 1 / 助手 1")
    query = api.run_query(wid, app_id, "congested")
    edges = query.get("expand", [{}])[0].get("objects", []) if query.get("expand") else []
    ok(f"run_query('congested') → {[o['id'] for o in query['objects']]}，"
       f"expand has_lane {len(edges)} 条边")
    if not args.no_agent:
        run = api.run_app_agent(wid, app_id, "值班助手", max_steps=4)
        ok(f"run_agent('值班助手') ok={run.get('ok')} "
           f"steps={len(run.get('steps', []))}")
        ok(f"answer：{str(run.get('answer', ''))[:180]}")

    section("STEP 5c · 智能问答 + 通用 Agent（intelligence）")
    asked = api.ask(wid, "济南西收费站现在为什么拥堵？")
    ok(f"ask 命中术语 {[hit.get('term') for hit in asked.get('concepts', [])]} → "
       f"对象 {[obj['id'] for obj in asked.get('objects', [])]}，"
       f"related {len(asked.get('related', []))} 条")
    ok(f"answer：{str(asked.get('answer', ''))[:160]}")
    if not args.no_agent:
        agent_run = api.agent(wid, "找出当前拥堵的收费站，说明原因并给出一条处置建议",
                              max_steps=5)
        ok(f"/agent ok={agent_run.get('ok')} "
           f"steps={len(agent_run.get('steps', []))}")
        ok(f"answer：{str(agent_run.get('answer', ''))[:200]}")

    section("STEP 5d · 治理：成员角色 / 权限 / 审计")
    api.add_member(wid, "ops", "editor")
    api.add_member(wid, "auditor", "viewer")
    members = api.workspace(wid).get("members") or {}
    ok(f"成员：{json.dumps(members, ensure_ascii=False)}")
    try:
        api.invoke(wid, "TollStation.congestion_scan", "TollStation/ST001",
                   user="auditor")
        raise SystemExit("FAIL: viewer 竟然能调能力")
    except RuntimeError as exc:
        ok(f"viewer 调能力被拒 → {str(exc).split(': ', 1)[-1][:120]}")
    editor = api.invoke(wid, "TollStation.congestion_scan", "TollStation/ST002",
                        user="ops")
    ok(f"editor 调能力 ok={editor['ok']} → {compact(editor['output'], 120)}")
    entries = api.audit(wid)
    actions: dict[str, int] = {}
    for entry in entries:
        actions[entry["action"]] = actions.get(entry["action"], 0) + 1
    ok(f"审计 {len(entries)} 条：{json.dumps(actions, ensure_ascii=False)}")

    print(f"\n控制台链路验证完成，用时 {time.monotonic() - started:.1f}s；"
          f"工作空间 wid = {wid}")
    print(f"打开 {args.base_url.rstrip('/')}/ 刷新工作台即可看到 STEP 3–5 全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
