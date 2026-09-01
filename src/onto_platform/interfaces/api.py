"""REST API adapter (stdlib only).

All routes live under /api; the web console is served at /.
Design-time resources and runtime state are exposed through the same
use cases the CLI uses - no domain logic here.

    GET  /api/health
    GET  /api/workspaces                        POST /api/workspaces
    GET  /api/workspaces/{wid}
    GET  /api/workspaces/{wid}/ontology         POST (same)  {name}
    POST /api/workspaces/{wid}/ontology/objects {name, properties, description}
    POST /api/workspaces/{wid}/ontology/relations
    POST /api/workspaces/{wid}/ontology/vocabulary
    POST /api/workspaces/{wid}/ontology/validate | /publish
    GET  /api/workspaces/{wid}/runtime          (kernel describe)
    GET  /api/workspaces/{wid}/datasources      POST (same)
    GET  /api/workspaces/{wid}/projections      POST (same)
    POST /api/workspaces/{wid}/projections/{pid}/run
    GET  /api/workspaces/{wid}/capabilities     POST (same)
    POST /api/workspaces/{wid}/capabilities/{cid}/publish
    POST /api/workspaces/{wid}/invoke           {function, target, args}
    GET  /api/workspaces/{wid}/applications     POST (same)
    POST /api/workspaces/{wid}/applications/{aid}/publish
    POST /api/workspaces/{wid}/applications/{aid}/query  {alias}
    GET  /api/workspaces/{wid}/state/find?type=X&prop__op=v
    GET  /api/workspaces/{wid}/state/get?ref=Type/Id
    GET  /api/workspaces/{wid}/state/related?ref=&relation=&direction=
    GET  /api/workspaces/{wid}/changes?after=0
    POST /api/workspaces/{wid}/ask              {question}
    POST /api/workspaces/{wid}/agent            {goal}
    GET  /api/workspaces/{wid}/audit
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..bootstrap import Platform, PlatformConfig, build_platform
from ..errors import (
    AccessDeniedError,
    ConnectorError,
    LifecycleError,
    NotFoundError,
    PlatformError,
    RuntimeUnavailableError,
    ValidationError,
)
from ..infrastructure.llm.echo import EchoLLMProvider
from ..infrastructure.llm.openai_compat import OpenAICompatProvider

_STATUS_BY_ERROR = (
    (NotFoundError, 404),
    (ValidationError, 400),
    (LifecycleError, 409),
    (AccessDeniedError, 403),
    (RuntimeUnavailableError, 503),
    (ConnectorError, 502),
)

WEB_DIR = Path(__file__).resolve().parent / "web"
# The React console is built into ``web/dist`` (see frontend/). Serving it from
# the package keeps ``python -m onto_platform.interfaces.api`` the one-command
# startup: no Node, no separate dev server, no second process at runtime.
DIST_DIR = WEB_DIR / "dist"

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _status_for(exc: Exception) -> int:
    if isinstance(exc, PlatformError):
        for cls, code in _STATUS_BY_ERROR:
            if isinstance(exc, cls):
                return code
        return 400
    return 500


def _parse_scalar(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def build_handler(platform: Platform) -> type[BaseHTTPRequestHandler]:
    class PlatformHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter tests
            pass

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: Exception) -> None:
            self._send(_status_for(exc), {"error": str(exc),
                                          "type": type(exc).__name__})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValidationError("request body must be a JSON object")
            return data

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()

        # ------------------------------------------------------------ GET

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            query = {k: _parse_scalar(v[-1]) for k, v in parse_qs(parsed.query).items()}
            try:
                if not parts or parts[0] != "api":
                    return self._serve_web(parts)
                if parts == ["api", "health"]:
                    provider = platform.ctx.llm
                    return self._send(200, {
                        "status": "ok",
                        "version": "0.1.0",
                        "llm_configured": provider is not None,
                        "backend": platform.config.store,
                        "runtime_store": platform.config.runtime_store or platform.config.store,
                        "llm_provider": type(provider).__name__ if provider else "none",
                        "console": "react" if (DIST_DIR / "index.html").is_file() else "legacy",
                    })
                if parts[:2] != ["api", "workspaces"]:
                    raise ValidationError(f"no route for {self.path}")
                wid = parts[2] if len(parts) > 2 else None
                rest = parts[3:]
                return self._get(wid, rest, query, parsed.query)
            except PlatformError as exc:
                return self._error(exc)
            except Exception as exc:  # pragma: no cover
                return self._error(exc)

        def _serve_web(self, parts: list[str]) -> None:
            """Serve the built React console from ``web/dist``.

            Any non-``/api`` path resolves inside ``dist``; unknown paths fall
            back to ``dist/index.html`` so client-side routes work. When the
            bundle has not been built we fall back to the legacy single-file
            console so the platform still starts and stays usable.
            """
            index = DIST_DIR / "index.html"
            if index.is_file():
                root = DIST_DIR.resolve()
                if parts:
                    candidate = (root / "/".join(parts)).resolve()
                    if candidate.is_file() and str(candidate).startswith(str(root)):
                        return self._send_file(candidate)
                return self._send_file(index)
            legacy = WEB_DIR / "index.html"
            if legacy.is_file():
                return self._send_file(legacy)
            return self._send(404, {"error": "web console not found"})

        def _send_file(self, path: Path) -> None:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _MIME.get(path.suffix.lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",
                             "public, max-age=31536000, immutable"
                             if "assets" in path.parts and path.suffix != ".html"
                             else "no-cache")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _get(self, wid: str | None, rest: list[str], query: dict,
                 raw_query: str = "") -> None:
            user = query.get("user", "admin")
            if wid is None:
                if rest == []:
                    return self._send(200, platform.workspaces.list(actor=user))
                raise ValidationError(f"no route")
            if rest == []:
                return self._send(200, platform.workspaces.get(wid, actor=user))
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.get(wid, actor=user))
            if rest == ["runtime"]:
                return self._send(200, platform.ontologies.describe_runtime(wid, actor=user))
            if rest == ["datasources"]:
                return self._send(200, platform.data.list_sources(wid, actor=user))
            if rest == ["projections"]:
                return self._send(200, platform.data.list_projections(wid, actor=user))
            if rest == ["capabilities"]:
                return self._send(200, platform.capabilities.list(wid, actor=user))
            if rest == ["applications"]:
                return self._send(200, platform.applications.list(wid, actor=user))
            if rest and rest[0] == "applications" and len(rest) == 2:
                return self._send(200, platform.applications.get(wid, rest[1], actor=user))
            if rest == ["state", "find"]:
                object_type = query.get("type")
                if not object_type:
                    raise ValidationError("query param 'type' is required")
                reserved = {"type", "user"}
                raw_pairs = {k: v[-1] for k, v in parse_qs(raw_query).items()}
                filters = {}
                for k, v in query.items():
                    if k in reserved:
                        continue
                    op = k.partition("__")[2] or "eq"
                    if op == "in":
                        # 集合语义：客户端把列表序列化成 "a,b"；必须拆回列表，
                        # 否则内核的 "actual not in expected" 对字符串退化成子串匹配
                        text = raw_pairs.get(k, "") if isinstance(v, (int, float)) else str(v)
                        items = [_parse_scalar(p.strip()) for p in text.split(",") if p.strip()]
                        filters[k] = items or [text]
                    elif op == "contains" and isinstance(v, (int, float)):
                        # text operators must receive the raw string: a
                        # numeric-looking needle (plate__contains=1001) must
                        # not become an int, or matching a string property
                        # raises TypeError inside the kernel and yields 0 rows
                        filters[k] = raw_pairs.get(k, str(v))
                    else:
                        filters[k] = v
                return self._send(200, gateway_of(platform, wid).find_objects(object_type, filters))
            if rest == ["state", "get"]:
                return self._send(200, gateway_of(platform, wid).get_object(query.get("ref", "")))
            if rest == ["state", "related"]:
                return self._send(200, gateway_of(platform, wid).related(
                    query.get("ref", ""), query.get("relation", ""),
                    direction=query.get("direction", "out")))
            if rest == ["changes"]:
                return self._send(200, gateway_of(platform, wid).observe_changes(
                    int(query.get("after", 0))))
            if rest == ["audit"]:
                return self._send(200, platform.ctx.audit.list(wid, limit=int(query.get("limit", 50))))
            raise ValidationError(f"no route for {self.path}")

        # ------------------------------------------------------------ POST

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            try:
                body = self._body()
                if parts[:2] != ["api", "workspaces"]:
                    raise ValidationError(f"no route for {self.path}")
                wid = parts[2] if len(parts) > 2 else None
                rest = parts[3:]
                return self._post(wid, rest, body)
            except PlatformError as exc:
                return self._error(exc)
            except (KeyError, json.JSONDecodeError) as exc:
                return self._error(ValidationError(f"invalid request body: {exc}"))
            except Exception as exc:  # pragma: no cover
                return self._error(exc)

        def _post(self, wid: str | None, rest: list[str], body: dict) -> None:
            user = body.pop("user", "admin")
            if wid is None:
                if rest == []:
                    return self._send(200, platform.workspaces.create(
                        body.get("name", ""), actor=user,
                        description=body.get("description", "")))
                raise ValidationError("no route")
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.create(
                    wid, body.get("name", ""), actor=user,
                    description=body.get("description", "")))
            if rest == ["ontology", "objects"]:
                return self._send(200, platform.ontologies.upsert_object(
                    wid, user, body["name"], properties=body.get("properties", []),
                    description=body.get("description", "")))
            if rest == ["ontology", "relations"]:
                return self._send(200, platform.ontologies.upsert_relation(
                    wid, user, body["name"], body["source"], body["target"],
                    description=body.get("description", "")))
            if rest == ["ontology", "vocabulary"]:
                return self._send(200, platform.ontologies.add_term(
                    wid, user, body["term"], body["concept"],
                    kind=body.get("kind", "object"), aliases=body.get("aliases")))
            if rest == ["ontology", "validate"]:
                problems = platform.ontologies.validate(wid, user)
                return self._send(200, {"ok": not problems, "problems": problems})
            if rest == ["ontology", "publish"]:
                return self._send(200, platform.ontologies.publish(wid, user))
            if rest == ["datasources"]:
                return self._send(200, platform.data.connect_source(
                    wid, user, body["name"], body.get("kind", "static"),
                    config=body.get("config", {}), assets=body.get("assets", [])))
            if rest == ["projections"]:
                return self._send(200, platform.data.create_projection(
                    wid, user, body["name"], body["datasource_id"],
                    object_mappings=body.get("object_mappings", []),
                    relation_mappings=body.get("relation_mappings", [])))
            if len(rest) == 3 and rest[0] == "projections" and rest[2] == "run":
                return self._send(200, platform.data.run_projection(wid, rest[1], user))
            if rest == ["capabilities"]:
                return self._send(200, platform.capabilities.create(
                    wid, user, key=body["key"], name=body["name"],
                    target_type=body["target_type"],
                    description=body.get("description", ""),
                    inputs=body.get("inputs", []),
                    implementation=body.get("implementation")))
            if len(rest) == 3 and rest[0] == "capabilities" and rest[2] == "publish":
                return self._send(200, platform.capabilities.publish(wid, rest[1], user))
            if rest == ["invoke"]:
                return self._send(200, platform.capabilities.invoke(
                    wid, user, body["function"], target=body.get("target"),
                    args=body.get("args", {})))
            if rest == ["applications"]:
                return self._send(200, platform.applications.create(
                    wid, user, body["name"], description=body.get("description", "")))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "publish":
                return self._send(200, platform.applications.publish(wid, rest[1], user))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "views":
                return self._send(200, platform.applications.add_view(
                    wid, rest[1], user, body["alias"], title=body.get("title", ""),
                    kind=body.get("kind", "table"),
                    object_type=body.get("object_type", ""),
                    properties=body.get("properties"),
                    query_alias=body.get("query_alias", "")))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "queries":
                return self._send(200, platform.applications.add_query(
                    wid, rest[1], user, body["alias"], body["object_type"],
                    filters=body.get("filters"), expand=body.get("expand")))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "capabilities":
                return self._send(200, platform.applications.attach_capability(
                    wid, rest[1], user, body["capability_id"]))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "agents":
                return self._send(200, platform.applications.add_agent(
                    wid, rest[1], user, body["alias"], goal=body.get("goal", ""),
                    description=body.get("description", "")))
            if len(rest) == 5 and rest[0] == "applications" and rest[2] == "agents" \
                    and rest[4] == "agent":
                return self._send(200, platform.applications.run_agent(
                    wid, rest[1], user, rest[3],
                    goal_override=body.get("goal", ""),
                    provider=_provider_from(body),
                    max_steps=int(body.get("max_steps", 8))))
            if len(rest) == 4 and rest[0] == "applications" and rest[3] == "agent":
                return self._send(200, platform.applications.run_agent(
                    wid, rest[1], user, rest[2],
                    goal_override=body.get("goal", ""),
                    provider=_provider_from(body),
                    max_steps=int(body.get("max_steps", 8))))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "query":
                return self._send(200, platform.applications.run_query(
                    wid, rest[1], user, body.get("alias", "")))
            if rest == ["ask"]:
                return self._send(200, platform.intelligence.ask(
                    wid, user, body.get("question", "")))
            if rest == ["agent"]:
                provider = _provider_from(body)
                return self._send(200, platform.intelligence.agent(
                    wid, user, body.get("goal", ""), provider=provider,
                    max_steps=int(body.get("max_steps", 8))))
            raise ValidationError(f"no route for {self.path}")

    return PlatformHTTPHandler


def gateway_of(platform: Platform, workspace_id: str):
    return platform.ctx.gateway_for(workspace_id)


def _provider_from(body: dict):
    """Build a provider from the request body, or None to let the use case
    fall back to the platform-configured provider."""
    llm = body.get("llm") or {}
    if llm.get("provider") == "openai":
        return OpenAICompatProvider(
            base_url=llm.get("base_url", "https://api.openai.com/v1"),
            model=llm.get("model", "gpt-4o-mini"),
            api_key=llm.get("api_key", ""),
        )
    if "script" in llm:
        return EchoLLMProvider(list(llm.get("script", [])))
    return None


def serve(platform: Platform, host: str = "127.0.0.1", port: int = 8100) -> None:
    server = ThreadingHTTPServer((host, port), build_handler(platform))
    console = "React (web/dist)" if (DIST_DIR / "index.html").is_file() else "legacy (web/index.html)"
    if not (DIST_DIR / "index.html").is_file():
        # 打包/克隆后没先 build 前端时会静默降级，这里必须说清楚，
        # 否则用户以为跑的是新版控制台。
        print("WARN  web/dist not found - serving the legacy console. "
              "Run `npm --prefix frontend run build` to enable the React console.")
    print(f"onto-platform api on http://{host}:{port}  (web console at /, {console})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(prog="onto-api")
    parser.add_argument("--db", default=".onto/platform.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    platform = build_platform(PlatformConfig(store="sqlite", db_path=args.db))
    provider = provider_from_env()
    if provider is not None:
        platform.configure_llm(provider)
        print(f"llm: {type(provider).__name__} (from environment)")
    serve(platform, host=args.host, port=args.port)


def provider_from_env():
    """Configure the default LLM from the environment (no code change needed):

        ONTO_LLM_BASE_URL   OpenAI-compatible endpoint (implies provider on)
        ONTO_LLM_MODEL      model name          (default gpt-4o-mini)
        ONTO_LLM_API_KEY    bearer key          (also accepts ONTO_LLM_KEY)
    """
    import os

    base_url = os.environ.get("ONTO_LLM_BASE_URL", "").strip()
    api_key = (os.environ.get("ONTO_LLM_API_KEY") or os.environ.get("ONTO_LLM_KEY") or "").strip()
    if not base_url and not api_key:
        return None
    return OpenAICompatProvider(
        base_url=base_url or "https://api.openai.com/v1",
        model=os.environ.get("ONTO_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        api_key=api_key,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
