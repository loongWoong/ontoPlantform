#!/usr/bin/env python3
"""高速收费站样例一键装载脚本。

    python bootstrap.py                       # 默认走运行中的 HTTP API（127.0.0.1:8100）
    python bootstrap.py --base-url http://127.0.0.1:8100
    python bootstrap.py --memory              # 不依赖服务，直接构建内存平台验证

流程（与平台「设计 → 发布 → 投影 → 状态」链路一致）：

    1. 创建工作空间「高速收费站样例」
    2. 创建本体，写入 expected-ontology.json 的对象 / 关系 / 术语
    3. validate 本体
    4. publish 本体（编译成 Kernel Schema）
    5. 连接 sqlite 数据源 data/toll.db
    6. 创建投影（对象映射 + 关系映射）
    7. 运行投影（数据写入 Runtime State）
    8. 回查 state/find，打印每个对象类型的对象数

重复执行是安全的：平台不提供按名查找工作空间，脚本每次都新建一个工作空间
并打印新的 wid（旧工作空间原样保留，不会改动）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Windows 控制台默认可能是 GBK，统一按 UTF-8 输出，避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "toll.db"
EXPECTED_FILE = ROOT / "expected-ontology.json"
BUSINESS_FILE = ROOT / "business.md"
DEFAULT_BASE_URL = "http://127.0.0.1:8100"
ACTOR = "admin"

# ---------------------------------------------------------------------------
# 数据源资产：每张表一条 SELECT 查询（sqlite 连接器只认 {"query": ...}）
# ---------------------------------------------------------------------------

ASSETS = [
    {
        "name": "stations",
        "description": "收费站快照",
        "config": {"query": "SELECT id, name, status, province FROM stations ORDER BY id"},
    },
    {
        "name": "lanes",
        "description": "收费车道",
        "config": {"query": "SELECT id, station_id, direction, lane_type FROM lanes ORDER BY id"},
    },
    {
        "name": "vehicles",
        "description": "车辆档案",
        "config": {"query": "SELECT plate, type, owner_type FROM vehicles ORDER BY plate"},
    },
    {
        "name": "passes",
        "description": "通行交易流水",
        "config": {
            "query": "SELECT id, plate, entry_station, exit_station, lane_id, fee, ts "
                     "FROM passes ORDER BY id"
        },
    },
]

# 对象映射：row -> 对象类型（id_field 提供对象 id，fields 是 源字段 -> 属性）
OBJECT_MAPPINGS = [
    {
        "asset": "stations",
        "object_type": "TollStation",
        "id_field": "id",
        "fields": [
            {"source_field": "name", "property": "name"},
            {"source_field": "status", "property": "status"},
            {"source_field": "province", "property": "province"},
        ],
    },
    {
        "asset": "lanes",
        "object_type": "Lane",
        "id_field": "id",
        "fields": [
            {"source_field": "direction", "property": "direction"},
            {"source_field": "lane_type", "property": "lane_type"},
            {"source_field": "station_id", "property": "station_id"},
        ],
    },
    {
        "asset": "vehicles",
        "object_type": "Vehicle",
        "id_field": "plate",
        "fields": [
            {"source_field": "plate", "property": "plate"},
            {"source_field": "type", "property": "type"},
            {"source_field": "owner_type", "property": "owner_type"},
        ],
    },
    {
        "asset": "passes",
        "object_type": "TollTransaction",
        "id_field": "id",
        "fields": [
            {"source_field": "plate", "property": "plate"},
            {"source_field": "entry_station", "property": "entry_station"},
            {"source_field": "exit_station", "property": "exit_station"},
            {"source_field": "lane_id", "property": "lane_id"},
            {"source_field": "fee", "property": "fee"},
            {"source_field": "ts", "property": "ts"},
        ],
    },
]

# 关系映射：行内两个端点 -> 关系边
RELATION_MAPPINGS = [
    {
        "asset": "lanes",
        "relation": "has_lane",
        "source_field": "station_id",
        "target_field": "id",
    },
    {
        "asset": "passes",
        "relation": "enters",
        "source_field": "plate",
        "target_field": "entry_station",
    },
    {
        "asset": "passes",
        "relation": "exits",
        "source_field": "id",
        "target_field": "exit_station",
    },
    {
        "asset": "passes",
        "relation": "passes",
        "source_field": "plate",
        "target_field": "exit_station",
    },
]


class BootstrapError(RuntimeError):
    """装载失败：带中文可操作提示。"""


# ---------------------------------------------------------------------------
# HTTP 后端（默认，对接运行中的 8100 服务）
# ---------------------------------------------------------------------------


class HttpBackend:
    label = "HTTP API"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None):
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(raw).get("error") or raw
            except (json.JSONDecodeError, AttributeError):
                detail = raw or "（无响应体）"
            raise BootstrapError(
                f"{method} {path} 失败：HTTP {exc.code}，{detail}"
            ) from exc
        except URLError as exc:
            raise BootstrapError(
                f"无法连接平台 API {self.base_url}（{exc.reason}）。"
                "请确认 onto-api 服务已启动：python -m onto_platform.interfaces.api --port 8100；"
                "或改用 python bootstrap.py --memory 离线验证。"
            ) from exc
        except TimeoutError as exc:
            raise BootstrapError(f"请求平台 API 超时：{method} {path}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BootstrapError(
                f"{method} {path} 返回的不是 JSON：{raw[:200]}"
            ) from exc

    # -- use cases ---------------------------------------------------------

    def create_workspace(self, name: str, description: str) -> dict:
        return self._request("POST", "/api/workspaces",
                             {"name": name, "description": description, "user": ACTOR})

    def create_ontology(self, wid: str, name: str, description: str) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/ontology",
                             {"name": name, "description": description, "user": ACTOR})

    def upsert_object(self, wid: str, spec: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/ontology/objects", {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "properties": spec.get("properties", []),
            "user": ACTOR,
        })

    def upsert_relation(self, wid: str, spec: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/ontology/relations", {
            "name": spec["name"],
            "source": spec["source"],
            "target": spec["target"],
            "description": spec.get("description", ""),
            "user": ACTOR,
        })

    def add_term(self, wid: str, term: dict) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/ontology/vocabulary", {
            "term": term["term"],
            "concept": term["concept"],
            "kind": term.get("kind", "object"),
            "aliases": term.get("aliases", []),
            "user": ACTOR,
        })

    def draft_ontology(self, wid: str, text: str, name: str) -> dict:
        """一段话 -> 本体：交给平台 LLM 解析并直接写入草稿本体。"""
        return self._request("POST", f"/api/workspaces/{wid}/ontology/draft", {
            "text": text, "apply": True, "name": name, "user": ACTOR,
        })

    def get_ontology(self, wid: str) -> dict:
        return self._request("GET", f"/api/workspaces/{wid}/ontology")

    def validate(self, wid: str) -> list[str]:
        result = self._request("POST", f"/api/workspaces/{wid}/ontology/validate",
                               {"user": ACTOR})
        return list(result.get("problems", []))

    def publish(self, wid: str) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/ontology/publish",
                             {"user": ACTOR})

    def connect_source(self, wid: str, name: str, kind: str,
                       config: dict, assets: list[dict]) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/datasources", {
            "name": name, "kind": kind, "config": config,
            "assets": assets, "user": ACTOR,
        })

    def create_projection(self, wid: str, name: str, datasource_id: str,
                          object_mappings: list[dict],
                          relation_mappings: list[dict]) -> dict:
        return self._request("POST", f"/api/workspaces/{wid}/projections", {
            "name": name, "datasource_id": datasource_id,
            "object_mappings": object_mappings,
            "relation_mappings": relation_mappings, "user": ACTOR,
        })

    def run_projection(self, wid: str, projection_id: str) -> dict:
        return self._request(
            "POST", f"/api/workspaces/{wid}/projections/{projection_id}/run",
            {"user": ACTOR},
        )

    def find(self, wid: str, object_type: str) -> list[dict]:
        query = urlencode({"type": object_type})
        return self._request("GET", f"/api/workspaces/{wid}/state/find?{query}")

    def related(self, wid: str, ref: str, relation: str) -> list[str]:
        query = urlencode({"ref": ref, "relation": relation, "direction": "out"})
        return self._request("GET", f"/api/workspaces/{wid}/state/related?{query}")


# ---------------------------------------------------------------------------
# 内存后端（离线验证：不依赖 8100 服务，直接组成内存平台）
# ---------------------------------------------------------------------------


class MemoryBackend:
    label = "内存平台（--memory）"

    def __init__(self) -> None:
        try:
            from onto_platform.bootstrap import PlatformConfig, build_platform
        except ImportError as exc:  # pragma: no cover - depends on env
            raise BootstrapError(
                "导入 onto_platform 失败：请先 pip install -e F:/piAgent/ontoPlantform"
            ) from exc
        self.platform = build_platform(PlatformConfig(store="memory"))

    def create_workspace(self, name: str, description: str) -> dict:
        return self.platform.workspaces.create(name, actor=ACTOR,
                                               description=description)

    def create_ontology(self, wid: str, name: str, description: str) -> dict:
        return self.platform.ontologies.create(wid, name, actor=ACTOR,
                                               description=description)

    def upsert_object(self, wid: str, spec: dict) -> dict:
        return self.platform.ontologies.upsert_object(
            wid, ACTOR, spec["name"],
            properties=spec.get("properties", []),
            description=spec.get("description", ""),
        )

    def upsert_relation(self, wid: str, spec: dict) -> dict:
        return self.platform.ontologies.upsert_relation(
            wid, ACTOR, spec["name"], spec["source"], spec["target"],
            description=spec.get("description", ""),
            cardinality=spec.get("cardinality", ""),
        )

    def add_term(self, wid: str, term: dict) -> dict:
        return self.platform.ontologies.add_term(
            wid, ACTOR, term["term"], term["concept"],
            kind=term.get("kind", "object"), aliases=term.get("aliases", []),
        )

    def draft_ontology(self, wid: str, text: str, name: str) -> dict:
        """内存平台没有服务端模型，只有设置了 ONTO_LLM_* 才能用 --use-llm。"""
        from onto_platform.infrastructure.llm.factory import provider_from_env

        provider = provider_from_env()
        if provider is None:
            raise BootstrapError(
                "内存平台未配置 LLM（未检测到 ONTO_LLM_* 环境变量）。"
                "--use-llm 建议走默认的 8100 HTTP API（服务端已配置真实模型）；"
                "或改用 python bootstrap.py --memory 走 expected-ontology.json。"
            )
        self.platform.configure_llm(provider)
        return self.platform.ontologies.draft(
            wid, ACTOR, text, apply=True, name=name)

    def get_ontology(self, wid: str) -> dict:
        return self.platform.ontologies.get(wid, ACTOR)

    def validate(self, wid: str) -> list[str]:
        return list(self.platform.ontologies.validate(wid, ACTOR))

    def publish(self, wid: str) -> dict:
        return self.platform.ontologies.publish(wid, ACTOR)

    def connect_source(self, wid: str, name: str, kind: str,
                       config: dict, assets: list[dict]) -> dict:
        return self.platform.data.connect_source(
            wid, ACTOR, name, kind, config=config, assets=assets)

    def create_projection(self, wid: str, name: str, datasource_id: str,
                          object_mappings: list[dict],
                          relation_mappings: list[dict]) -> dict:
        return self.platform.data.create_projection(
            wid, ACTOR, name, datasource_id,
            object_mappings=object_mappings,
            relation_mappings=relation_mappings)

    def run_projection(self, wid: str, projection_id: str) -> dict:
        return self.platform.data.run_projection(wid, projection_id, ACTOR)

    def _gateway(self, wid: str):
        return self.platform.ctx.gateway_for(wid)

    def find(self, wid: str, object_type: str) -> list[dict]:
        return self._gateway(wid).find_objects(object_type)

    def related(self, wid: str, ref: str, relation: str) -> list[str]:
        return self._gateway(wid).related(ref, relation, direction="out")


# ---------------------------------------------------------------------------
# 装载流程
# ---------------------------------------------------------------------------


def load_expected() -> dict:
    if not EXPECTED_FILE.is_file():
        raise BootstrapError(f"缺少本体标准答案文件：{EXPECTED_FILE}")
    try:
        expected = json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"{EXPECTED_FILE.name} 不是合法 JSON：{exc}") from exc
    for key in ("objects", "relations", "vocabulary"):
        if not expected.get(key):
            raise BootstrapError(f"{EXPECTED_FILE.name} 缺少 {key!r} 部分")
    return expected


def step(index: int, total: int, message: str) -> None:
    print(f"[{index}/{total}] {message}")


def load_business_text() -> str:
    if not BUSINESS_FILE.is_file():
        raise BootstrapError(f"缺少业务说明文件：{BUSINESS_FILE}")
    text = BUSINESS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        raise BootstrapError(f"{BUSINESS_FILE.name} 是空的，无法交给 LLM 解析")
    return text


def filter_mappings(object_mappings: list[dict], relation_mappings: list[dict],
                    ontology: dict) -> tuple[list[dict], list[dict], list[str]]:
    """LLM 解析出的对象名未必与 expected-ontology.json 完全一致。

    这里按「实际本体」裁剪投影映射：本体里没有的对象类型 / 属性 / 关系直接跳过，
    并返回中文说明，让 --use-llm 模式尽量跑通而不是硬失败。
    """
    objects = ontology.get("objects", {}) or {}
    relations = ontology.get("relations", {}) or {}
    notes: list[str] = []

    kept_objects: list[dict] = []
    loaded_types: set[str] = set()
    for mapping in object_mappings:
        type_name = mapping["object_type"]
        spec = objects.get(type_name)
        if spec is None:
            notes.append(f"跳过对象映射 {type_name}：LLM 本体中没有该对象类型")
            continue
        available = set((spec.get("properties") or {}))
        fields = [f for f in mapping["fields"] if f["property"] in available]
        dropped = [f["property"] for f in mapping["fields"] if f["property"] not in available]
        if dropped:
            notes.append(f"{type_name} 跳过属性映射 {dropped}：LLM 本体未定义这些属性")
        kept_objects.append({**mapping, "fields": fields})
        loaded_types.add(type_name)

    kept_relations: list[dict] = []
    for mapping in relation_mappings:
        name = mapping["relation"]
        rdef = relations.get(name)
        if rdef is None:
            notes.append(f"跳过关系映射 {name}：LLM 本体中没有该关系")
            continue
        source, target = rdef.get("source"), rdef.get("target")
        if source not in loaded_types or target not in loaded_types:
            notes.append(
                f"跳过关系映射 {name}：端点 {source}→{target} 的对象没有被投影映射")
            continue
        kept_relations.append(mapping)
    return kept_objects, kept_relations, notes


def bootstrap(backend, expected: dict, use_llm: bool = False) -> dict:
    if not DB_PATH.is_file():
        raise BootstrapError(
            f"找不到样例数据库 {DB_PATH}，请先执行：python data/build_db.py"
        )

    total_steps = 8
    step(1, total_steps, "创建工作空间…")
    workspace = backend.create_workspace(
        expected.get("workspace_name", "高速收费站样例"),
        expected.get("description", ""),
    )
    wid = workspace["id"]
    print(f"      wid = {wid}")

    if use_llm:
        step(2, total_steps, "把 business.md 交给 LLM 解析本体"
                             "（POST /ontology/draft, apply=true）…")
        draft_result = backend.draft_ontology(
            wid, load_business_text(), expected.get("ontology_name", "toll"))
        if not draft_result.get("applied"):
            raise BootstrapError(
                "LLM 草稿没有写入本体（apply=true 时平台应自动创建草稿本体）")
        draft = draft_result.get("draft", {}) or {}
        warnings = list(draft_result.get("warnings") or [])
        print(f"      applied=True · 对象 {len(draft.get('objects', []))} · "
              f"关系 {len(draft.get('relations', []))} · "
              f"术语 {len(draft.get('vocabulary', []))} · warnings {len(warnings)}")
        for warning in warnings[:8]:
            print(f"      warning: {warning}")
        if len(warnings) > 8:
            print(f"      … 另有 {len(warnings) - 8} 条 warning")
        ontology = backend.get_ontology(wid)
        object_mappings, relation_mappings, notes = filter_mappings(
            OBJECT_MAPPINGS, RELATION_MAPPINGS, ontology)
        for note in notes:
            print(f"      投影映射调整：{note}")
        if notes:
            print("      提示：LLM 解析出的标识符与 expected-ontology.json 不完全一致，"
                  "本次按可用映射装载；默认模式（不加 --use-llm）可完整装载。")
        if not object_mappings:
            raise BootstrapError(
                "LLM 解析出的对象与 expected-ontology.json 没有交集，无法建立投影；"
                "请看上面的 warning，或调整 business.md / expected-ontology.json")
    else:
        step(2, total_steps, "创建本体并写入对象 / 关系 / 术语…")
        backend.create_ontology(wid, expected.get("ontology_name", "toll"),
                                expected.get("description", ""))
        for spec in expected["objects"]:
            backend.upsert_object(wid, spec)
        for spec in expected["relations"]:
            backend.upsert_relation(wid, spec)
        for term in expected["vocabulary"]:
            backend.add_term(wid, term)
        ontology = backend.get_ontology(wid)
        object_mappings, relation_mappings = OBJECT_MAPPINGS, RELATION_MAPPINGS
    print(f"      本体：对象 {len(ontology.get('objects', {}))} · "
          f"关系 {len(ontology.get('relations', {}))} · "
          f"术语 {len(ontology.get('vocabulary', {}))}")

    step(3, total_steps, "校验本体…")
    problems = backend.validate(wid)
    if problems:
        raise BootstrapError("本体校验未通过：" + "; ".join(problems))
    print("      校验通过")

    step(4, total_steps, "发布本体（编译成 Runtime Schema）…")
    published = backend.publish(wid)
    runtime = published.get("runtime", {}) if isinstance(published, dict) else {}
    runtime_objects = len(runtime.get("objects", {})) if isinstance(runtime, dict) else 0
    print(f"      已发布；Runtime 对象类型 {runtime_objects}")

    step(5, total_steps, f"连接 sqlite 数据源 {DB_PATH} …")
    source = backend.connect_source(
        wid, "收费流水库", "sqlite", {"path": str(DB_PATH)}, ASSETS)
    print(f"      datasource = {source['id']}（{len(ASSETS)} 个资产）")

    step(6, total_steps, "创建投影（对象映射 + 关系映射）…")
    projection = backend.create_projection(
        wid, "收费站主数据投影", source["id"],
        object_mappings, relation_mappings)
    print(f"      projection = {projection['id']}"
          f"（对象映射 {len(object_mappings)} · 关系映射 {len(relation_mappings)}）")

    step(7, total_steps, "运行投影（数据写入 Runtime State）…")
    summary = backend.run_projection(wid, projection["id"])
    print(f"      upserts={summary.get('objects_upserted')} "
          f"links={summary.get('links_asserted')} "
          f"changes={summary.get('changes')}")

    step(8, total_steps, "回查 Runtime State（state/find）…")
    counts: dict[str, int] = {}
    samples: dict[str, dict] = {}
    for name in ontology.get("objects", {}):
        rows = backend.find(wid, name)
        counts[name] = len(rows)
        if rows:
            samples[name] = rows[0]
        print(f"      {name:<16} {len(rows):>3} 个对象")

    report = {
        "mode": "llm" if use_llm else "expected",
        "backend": backend.label,
        "wid": wid,
        "object_types": len(ontology.get("objects", {})),
        "relations": len(ontology.get("relations", {})),
        "terms": len(ontology.get("vocabulary", {})),
        "datasource_id": source["id"],
        "projection_id": projection["id"],
        "upserts": summary.get("objects_upserted"),
        "links": summary.get("links_asserted"),
        "changes": summary.get("changes"),
        "counts": counts,
        "samples": samples,
    }
    _print_report(report)
    return report


def _print_report(report: dict) -> None:
    print("\n" + "=" * 62)
    print("装载完成")
    print(f"  后端       {report['backend']}")
    print(f"  本体来源   {'business.md → LLM 解析（--use-llm）' if report['mode'] == 'llm' else 'expected-ontology.json'}")
    print(f"  工作空间   wid = {report['wid']}")
    print(f"  对象类型   {report['object_types']}")
    print(f"  关系       {report['relations']}")
    print(f"  业务术语   {report['terms']}")
    print(f"  数据源     {report['datasource_id']}")
    print(f"  投影       {report['projection_id']}")
    print(f"  投影结果   upserts={report['upserts']}  links={report['links']}  "
          f"changes={report['changes']}")
    print("  对象计数   " + " · ".join(
        f"{name}={count}" for name, count in report["counts"].items()))
    sample = report["samples"].get("TollStation")
    if sample:
        print(f"  TollStation 样例  {sample['type']}/{sample['id']} "
              f"{json.dumps(sample['properties'], ensure_ascii=False)}")
    print("=" * 62)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把高速收费站样例装载进 OntoPlatform（默认对接 8100 HTTP API）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"平台 API 地址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument("--memory", action="store_true",
                        help="不连接服务，直接构建内存平台跑完整流程（离线验证）")
    parser.add_argument("--use-llm", action="store_true",
                        help="把 business.md 交给平台 LLM 解析本体（POST /ontology/draft，"
                             "需要服务端已配置模型；默认走 expected-ontology.json）")
    args = parser.parse_args(argv)

    try:
        expected = load_expected()
        backend = MemoryBackend() if args.memory else HttpBackend(args.base_url)
        if not args.memory:
            print(f"目标平台：{backend.base_url}")
        else:
            print("目标平台：内存平台（进程内，跑完即释放）")
        print("本体来源：" + ("business.md → LLM 解析（--use-llm）"
                            if args.use_llm else "expected-ontology.json（默认）"))
        bootstrap(backend, expected, use_llm=args.use_llm)
    except BootstrapError as exc:
        print(f"\n[装载失败] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[已中断]", file=sys.stderr)
        return 130
    except Exception as exc:  # 兜底：平台异常（ValidationError 等）也给出清晰信息
        print(f"\n[装载失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
