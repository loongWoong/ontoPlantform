"""OntoPlatform CLI adapter.

    onto workspace create 高速运营
    onto ontology add-object Vehicle --prop plate:string --prop balance:number
    onto ontology publish
    onto data add-source 交易库 --kind static --asset vehicles@rows.json
    onto data run-projection <id>
    onto capability create recharge --target Vehicle --source recharge.py --input amount:number
    onto capability publish <id>
    onto capability invoke Vehicle.recharge --target Vehicle/V001 --arg amount=100
    onto state find TollStation --filter status=congested
    onto ask "济南西收费站现在为什么拥堵？"
    onto agent run "..." [--provider echo|openai --base-url ... --model ...]
    onto serve --port 8100
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..bootstrap import PlatformConfig, build_platform
from ..errors import PlatformError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DB = ".onto/platform.db"
DEFAULT_USER = "admin"


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parse_scalar(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _pairs(values: list[str] | None) -> dict:
    result = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item!r}")
        key, _, raw = item.partition("=")
        result[key] = _parse_scalar(raw)
    return result


def _props(values: list[str] | None) -> list[dict]:
    """--prop plate:string --prop balance:number"""
    props = []
    for item in values or []:
        name, _, kind = item.partition(":")
        props.append({"name": name, "type": kind or "string"})
    return props


def _inputs(values: list[str] | None) -> list[dict]:
    return [{"name": name, "type": kind or "string"}
            for name, _, kind in (item.partition(":") for item in (values or []))]


def _read_rows(path: str | None) -> list[dict]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [dict(row) for row in data]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onto", description="OntoPlatform")
    parser.add_argument("--db", default=DEFAULT_DB, help="platform sqlite database path")
    parser.add_argument("--workspace", help="workspace id (required per command)")
    parser.add_argument("--user", default=DEFAULT_USER, help="acting user")
    parser.add_argument("--runtime-store", default="auto",
                        choices=["auto", "memory", "sqlite"],
                        help="kernel state store for NEW workspaces "
                             "(auto: follow the platform store)")
    sub = parser.add_subparsers(dest="command", required=True)

    # workspace
    p = sub.add_parser("workspace")
    wsp = p.add_subparsers(dest="subcommand", required=True)
    wsp_create = wsp.add_parser("create")
    wsp_create.add_argument("name")
    wsp_create.add_argument("--description", default="")
    wsp.add_parser("list")

    # ontology
    p = sub.add_parser("ontology")
    onto = p.add_subparsers(dest="subcommand", required=True)
    onto_create = onto.add_parser("create")
    onto_create.add_argument("name")
    onto_obj = onto.add_parser("add-object")
    onto_obj.add_argument("name")
    onto_obj.add_argument("--prop", action="append", dest="props")
    onto_obj.add_argument("--description", default="")
    onto_rel = onto.add_parser("add-relation")
    onto_rel.add_argument("name")
    onto_rel.add_argument("source")
    onto_rel.add_argument("target")
    onto_term = onto.add_parser("add-term")
    onto_term.add_argument("term")
    onto_term.add_argument("concept")
    onto_term.add_argument("--kind", default="object",
                           choices=["object", "property", "relation"])
    onto_draft = onto.add_parser("draft")
    onto_draft.add_argument("--text", default="", help="业务说明文本")
    onto_draft.add_argument("--file", help="从文件读取业务说明（markdown/txt）")
    onto_draft.add_argument("--apply", action="store_true",
                            help="写入草稿本体（默认只解析预览）")
    onto_draft.add_argument("--name", default="ai-draft",
                            help="工作空间尚无本体时新建的本体名")
    onto.add_parser("show")
    onto.add_parser("validate")
    onto.add_parser("publish")
    onto.add_parser("describe-runtime")

    # data
    p = sub.add_parser("data")
    data = p.add_subparsers(dest="subcommand", required=True)
    src = data.add_parser("add-source")
    src.add_argument("name")
    src.add_argument("--kind", default="static", choices=["static", "sqlite", "rest"])
    src.add_argument("--config", default="{}", help="JSON config")
    src.add_argument("--asset", action="append", dest="assets",
                     help="name=@rows.json (static) or name=query.sql path")
    proj = data.add_parser("add-projection")
    proj.add_argument("name")
    proj.add_argument("--source", required=True, help="datasource id")
    proj.add_argument("--object-mapping", action="append", dest="object_mappings",
                      help="JSON object mapping")
    proj.add_argument("--relation-mapping", action="append", dest="relation_mappings",
                      help="JSON relation mapping")
    data.add_parser("list-sources")
    data.add_parser("list-projections")
    proj_run = data.add_parser("run-projection")
    proj_run.add_argument("projection_id")

    # capability
    p = sub.add_parser("capability")
    cap = p.add_subparsers(dest="subcommand", required=True)
    cap_create = cap.add_parser("create")
    cap_create.add_argument("key")
    cap_create.add_argument("--name", required=True)
    cap_create.add_argument("--target", required=True, help="object type")
    cap_create.add_argument("--description", default="")
    cap_create.add_argument("--input", action="append", dest="inputs")
    cap_create.add_argument("--source", help="python implementation file")
    cap_create.add_argument("--workflow", help="workflow steps JSON file")
    cap.add_parser("list")
    cap_pub = cap.add_parser("publish")
    cap_pub.add_argument("capability_id")
    cap_inv = cap.add_parser("invoke")
    cap_inv.add_argument("function")
    cap_inv.add_argument("--target")
    cap_inv.add_argument("--arg", action="append", dest="args")

    # application
    p = sub.add_parser("app")
    app = p.add_subparsers(dest="subcommand", required=True)
    app_create = app.add_parser("create")
    app_create.add_argument("name")
    app_view = app.add_parser("add-view")
    app_view.add_argument("app_id")
    app_view.add_argument("alias")
    app_view.add_argument("--kind", default="table")
    app_view.add_argument("--object-type", default="")
    app_view.add_argument("--query", default="")
    app_query = app.add_parser("add-query")
    app_query.add_argument("app_id")
    app_query.add_argument("alias")
    app_query.add_argument("--object-type", required=True)
    app_query.add_argument("--filter", action="append", dest="filters")
    app_pub = app.add_parser("publish")
    app_pub.add_argument("app_id")
    app_run = app.add_parser("run-query")
    app_run.add_argument("app_id")
    app_run.add_argument("alias")
    app_agent = app.add_parser("add-agent")
    app_agent.add_argument("app_id")
    app_agent.add_argument("alias")
    app_agent.add_argument("--goal", required=True)
    app_agent.add_argument("--description", default="")
    app_runagent = app.add_parser("run-agent")
    app_runagent.add_argument("app_id")
    app_runagent.add_argument("alias")
    app_runagent.add_argument("--goal", default="")

    # runtime state
    p = sub.add_parser("state")
    state = p.add_subparsers(dest="subcommand", required=True)
    state_find = state.add_parser("find")
    state_find.add_argument("object_type")
    state_find.add_argument("--filter", action="append", dest="filters")
    state_get = state.add_parser("get")
    state_get.add_argument("ref")
    state_rel = state.add_parser("related")
    state_rel.add_argument("ref")
    state_rel.add_argument("relation")
    state_rel.add_argument("--direction", default="out", choices=["out", "in"])
    changes = state.add_parser("changes")
    changes.add_argument("--after", type=int, default=0)

    # intelligence
    p = sub.add_parser("ask")
    p.add_argument("question")
    p = sub.add_parser("agent")
    agent = p.add_subparsers(dest="subcommand", required=True)
    agent_run = agent.add_parser("run")
    agent_run.add_argument("goal")
    agent_run.add_argument("--provider", default="echo", choices=["echo", "openai"])
    agent_run.add_argument("--base-url", default="https://api.openai.com/v1")
    agent_run.add_argument("--model", default="gpt-4o-mini")
    agent_run.add_argument("--api-key", default="")

    # governance
    p = sub.add_parser("audit")
    p.add_argument("--limit", type=int, default=30)

    # serve
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = PlatformConfig(store="sqlite", db_path=args.db,
                            runtime_store=None if args.runtime_store == "auto"
                            else args.runtime_store)
    platform = build_platform(config)
    user = args.user

    try:
        if args.command == "workspace":
            if args.subcommand == "create":
                _emit(platform.workspaces.create(args.name, actor=user,
                                                 description=args.description))
            elif args.subcommand == "list":
                _emit(platform.workspaces.list(actor=user))
        elif args.command == "ontology":
            wid = _need_workspace(args)
            if args.subcommand == "create":
                _emit(platform.ontologies.create(wid, args.name, actor=user))
            elif args.subcommand == "add-object":
                _emit(platform.ontologies.upsert_object(
                    wid, user, args.name, properties=_props(args.props),
                    description=args.description))
            elif args.subcommand == "add-relation":
                _emit(platform.ontologies.upsert_relation(
                    wid, user, args.name, args.source, args.target))
            elif args.subcommand == "add-term":
                _emit(platform.ontologies.add_term(
                    wid, user, args.term, args.concept, kind=args.kind))
            elif args.subcommand == "draft":
                text = args.text
                if args.file:
                    text = Path(args.file).read_text(encoding="utf-8")
                from .api import provider_from_env

                _emit(platform.ontologies.draft(
                    wid, user, text, apply=args.apply,
                    provider=provider_from_env(), name=args.name))
            elif args.subcommand == "show":
                _emit(platform.ontologies.get(wid, user))
            elif args.subcommand == "validate":
                problems = platform.ontologies.validate(wid, user)
                _emit({"problems": problems, "ok": not problems})
            elif args.subcommand == "publish":
                _emit(platform.ontologies.publish(wid, user))
            elif args.subcommand == "describe-runtime":
                _emit(platform.ontologies.describe_runtime(wid, user))
        elif args.command == "data":
            wid = _need_workspace(args)
            if args.subcommand == "add-source":
                assets = []
                for item in args.assets or []:
                    name, _, path = item.partition("=")
                    if name.startswith("static:") or path.startswith("@"):
                        rows = _read_rows(path.lstrip("@"))
                        assets.append({"name": name, "config": {"rows": rows}})
                    else:
                        assets.append({"name": name, "config": {"query": path}})
                _emit(platform.data.connect_source(
                    wid, user, args.name, args.kind,
                    config=json.loads(args.config), assets=assets))
            elif args.subcommand == "add-projection":
                _emit(platform.data.create_projection(
                    wid, user, args.name, args.source,
                    object_mappings=[json.loads(m) for m in args.object_mappings or []],
                    relation_mappings=[json.loads(m) for m in args.relation_mappings or []]))
            elif args.subcommand == "list-sources":
                _emit(platform.data.list_sources(wid, user))
            elif args.subcommand == "list-projections":
                _emit(platform.data.list_projections(wid, user))
            elif args.subcommand == "run-projection":
                _emit(platform.data.run_projection(wid, args.projection_id, user))
        elif args.command == "capability":
            wid = _need_workspace(args)
            if args.subcommand == "create":
                implementation = None
                if args.source:
                    implementation = {"kind": "python",
                                      "source": Path(args.source).read_text(encoding="utf-8")}
                elif args.workflow:
                    implementation = {
                        "kind": "workflow",
                        "steps": json.loads(Path(args.workflow).read_text(encoding="utf-8")),
                    }
                _emit(platform.capabilities.create(
                    wid, user, key=args.key, name=args.name, target_type=args.target,
                    description=args.description, inputs=_inputs(args.inputs),
                    implementation=implementation))
            elif args.subcommand == "list":
                _emit(platform.capabilities.list(wid, user))
            elif args.subcommand == "publish":
                _emit(platform.capabilities.publish(wid, args.capability_id, user))
            elif args.subcommand == "invoke":
                _emit(platform.capabilities.invoke(
                    wid, user, args.function, target=args.target,
                    args=_pairs(args.args)))
        elif args.command == "app":
            wid = _need_workspace(args)
            if args.subcommand == "create":
                _emit(platform.applications.create(wid, user, args.name))
            elif args.subcommand == "add-view":
                _emit(platform.applications.add_view(
                    wid, args.app_id, user, args.alias, kind=args.kind,
                    object_type=args.object_type, query_alias=args.query))
            elif args.subcommand == "add-query":
                _emit(platform.applications.add_query(
                    wid, args.app_id, user, args.alias, args.object_type,
                    filters=_pairs(args.filters)))
            elif args.subcommand == "publish":
                _emit(platform.applications.publish(wid, args.app_id, user))
            elif args.subcommand == "run-query":
                _emit(platform.applications.run_query(wid, args.app_id, user, args.alias))
            elif args.subcommand == "add-agent":
                _emit(platform.applications.add_agent(
                    wid, args.app_id, user, args.alias, goal=args.goal,
                    description=args.description))
            elif args.subcommand == "run-agent":
                _emit(platform.applications.run_agent(
                    wid, args.app_id, user, args.alias, goal_override=args.goal))
        elif args.command == "state":
            wid = _need_workspace(args)
            gateway = platform.ctx.gateway_for(wid)
            if args.subcommand == "find":
                _emit(gateway.find_objects(args.object_type, _pairs(args.filters)))
            elif args.subcommand == "get":
                _emit(gateway.get_object(args.ref))
            elif args.subcommand == "related":
                _emit(gateway.related(args.ref, args.relation, args.direction))
            elif args.subcommand == "changes":
                # cross-process view: the audit log is the durable record of
                # runtime changes (in-process streams live on the gateway)
                events = []
                for entry in platform.ctx.audit.list(wid, limit=200):
                    if entry["action"] == "capability.invoke":
                        events.append({"ts": entry["ts"], "source": "invoke",
                                       "actor": entry["actor"],
                                       "changes": entry["detail"].get("changes", [])})
                    elif entry["action"] == "data.run_projection":
                        events.append({"ts": entry["ts"], "source": "projection",
                                       "actor": entry["actor"],
                                       "changes": entry["detail"]})
                _emit(list(reversed(events)))  # chronological
        elif args.command == "ask":
            wid = _need_workspace(args)
            _emit(platform.intelligence.ask(wid, user, args.question))
        elif args.command == "agent":
            wid = _need_workspace(args)
            if args.subcommand == "run":
                provider = _make_provider(args)
                _emit(platform.intelligence.agent(wid, user, args.goal, provider=provider))
        elif args.command == "audit":
            wid = args.workspace
            _emit(platform.ctx.audit.list(wid, limit=args.limit))
        elif args.command == "serve":
            from .api import serve

            serve(platform, host=args.host, port=args.port)
    except PlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _make_provider(args):
    from ..infrastructure.llm.echo import EchoLLMProvider
    from ..infrastructure.llm.openai_compat import OpenAICompatProvider

    if args.provider == "echo":
        return EchoLLMProvider([])
    return OpenAICompatProvider(base_url=args.base_url, model=args.model,
                                api_key=args.api_key)


def _need_workspace(args) -> str:
    if not args.workspace:
        raise SystemExit("this command requires --workspace <id>")
    return args.workspace


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
