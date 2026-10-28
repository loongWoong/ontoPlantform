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
    POST /api/workspaces/{wid}/ontology/validate | /publish | /lint | /revisions
    GET  /api/workspaces/{wid}/state/history?ref=&limit=
    GET  /api/workspaces/{wid}/state/as-of?ref=&seq=   (or &at=RFC3339)
    GET  /api/workspaces/{wid}/runtime          (kernel describe)
    GET  /api/workspaces/{wid}/datasources      POST (same)
    GET  /api/workspaces/{wid}/projections      POST (same)
    POST /api/workspaces/{wid}/projections/{pid}/run
    GET  /api/workspaces/{wid}/capabilities     POST (same)
    POST /api/workspaces/{wid}/capabilities/{cid}/publish
    POST /api/workspaces/{wid}/invoke           {function, target, args}
    GET  /api/workspaces/{wid}/releases         POST (same) {note}
    POST /api/workspaces/{wid}/releases/{rid}/deploy
    POST /api/workspaces/{wid}/releases/{rid}/canary
    POST /api/workspaces/{wid}/releases/canary/promote {require_healthy?}
    POST /api/workspaces/{wid}/releases/rollback {release_id?}
    GET  /api/workspaces/{wid}/events?limit=200&type=
    POST /api/workspaces/{wid}/events/dispatch
    GET  /api/workspaces/{wid}/applications     POST (same)
    POST /api/workspaces/{wid}/applications/{aid}/publish
    POST /api/workspaces/{wid}/applications/{aid}/query  {alias}
    POST /api/workspaces/{wid}/applications/{aid}/objective {metric,target,band,window}
    GET  /api/workspaces/{wid}/applications/{aid}/objective   (current/deviation/trend)
    GET  /api/workspaces/{wid}/todos?status=open
    POST /api/workspaces/{wid}/todos/{tid}/resolve
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
from .. import __version__
from ..errors import (
    AccessDeniedError,
    CompilerError,
    ConcurrencyConflictError,
    ConnectorError,
    LifecycleError,
    NotFoundError,
    PlatformError,
    RuntimeUnavailableError,
    ValidationError,
    classify,
)
from ..infrastructure.llm.echo import EchoLLMProvider
from ..infrastructure.llm.openai_compat import OpenAICompatProvider

import hmac
import os

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
    return classify(exc)["http_status"]


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
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Idempotency-Key, If-Match, Authorization")

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: Exception) -> None:
            details = classify(exc)
            body = json.dumps({
                "error": str(exc),
                "type": details["type"],
                "code": details["code"],
                "retryable": details["retryable"],
            }, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(details["http_status"])
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if details["code"] == "execution_in_progress":
                self.send_header("Retry-After", "2")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValidationError("request body must be a JSON object")
            return data

        def _actor(self, body: dict | None = None, query: dict | None = None) -> str:
            """Resolve the actor of one request.

            When ``ONTO_API_TOKEN`` is set the only accepted identity is the
            bearer token - a ``user`` field/param in the body or query string
            can no longer impersonate anyone (v0.8).  The token is mapped to
            an actor name by ``ONTO_API_TOKEN_ACTOR`` (default "api-client").

            Without a configured token the adapter runs in local single-user
            mode and keeps accepting the declared ``user`` for back-compat.
            """
            expected = os.environ.get("ONTO_API_TOKEN")
            if expected:
                header = (self.headers.get("Authorization") or "").strip()
                token = header[7:].strip() if header.lower().startswith("bearer ") else ""
                if not token or not hmac.compare_digest(token, expected):
                    raise AccessDeniedError("invalid or missing bearer token")
                return os.environ.get("ONTO_API_TOKEN_ACTOR", "api-client")
            # local single-user mode: loopback only.  The declared user is
            # accepted for developer ergonomics on this machine, never for a
            # remote client (P1-13).
            client = (self.client_address or ("",))[0]
            if client not in ("127.0.0.1", "::1", "localhost"):
                raise AccessDeniedError(
                    "token mode is required for non-loopback clients")
            local_actor = os.environ.get("ONTO_LOCAL_ACTOR")
            if local_actor:
                return local_actor
            if body is not None:
                return str(body.pop("user", "admin"))
            return str((query or {}).get("user", "admin"))

        def _expected_version(self, body: dict) -> int | None:
            """Optimistic-lock precondition: body field or ``If-Match`` header."""
            value = body.get("expected_version")
            if value is None:
                header = (self.headers.get("If-Match") or "").strip('" ')
                if header:
                    value = header
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValidationError(
                    f"expected_version must be an integer, got {value!r}"
                ) from None

        @staticmethod
        def _query_int(query: dict, key: str, default: int) -> int:
            value = query.get(key, default)
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValidationError(
                    f"query parameter {key!r} must be an integer, got {value!r}"
                ) from None

        def _window_seconds(self, query: dict) -> int | None:
            raw = query.get("window")
            if raw:
                aliases = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600,
                           "24h": 86400, "7d": 604800}
                if raw in aliases:
                    return aliases[raw]
                raise ValidationError(
                    f"unknown window {raw!r}; use 5m, 1h, 24h or 7d")
            value = query.get("window_seconds")
            if value:
                return self._query_int(query, "window_seconds", 3600)
            return None

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
                        "version": __version__,
                        "llm_configured": provider is not None,
                        "backend": platform.config.store,
                        "runtime_store": platform.config.runtime_store or platform.config.store,
                        "llm_provider": type(provider).__name__ if provider else "none",
                        "console": "react" if (DIST_DIR / "index.html").is_file() else "legacy",
                        "api_version": 3,
                        "auth_mode": (
                            "token" if os.environ.get("ONTO_API_TOKEN")
                            else "local"),
                        "features": [
                            "ontology.draft", "ontology.lint", "ontology.revision",
                            "releases", "canary.isolated", "events", "metrics",
                            "slo", "slo.windowed", "verify", "reconcile",
                            "controller", "objective", "execution.ledger",
                            "execution.resolve", "scheduler", "history",
                            "agent.runs",
                        ],
                    })
                if parts == ["api", "scheduler"]:
                    scheduler = platform.scheduler
                    return self._send(200, scheduler.status() if scheduler
                                      else {"running": False, "tasks": []})
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
            user = self._actor(query=query)
            if wid is None:
                if rest == []:
                    return self._send(200, platform.workspaces.list(actor=user))
                raise ValidationError(f"no route")
            if rest == []:
                return self._send(200, platform.workspaces.get(wid, actor=user))
            if rest == ["members"]:
                return self._send(200, platform.workspaces.list_members(
                    wid, actor=user))
            if rest == ["llm-config"]:
                return self._send(200, platform.llm_config.get(wid, user))
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.get(wid, actor=user))
            if rest == ["runtime"]:
                return self._send(200, platform.ontologies.describe_runtime(wid, actor=user))
            if rest == ["datasources"]:
                return self._send(200, platform.data.list_sources(wid, actor=user))
            if rest == ["projections"]:
                return self._send(200, platform.data.list_projections(wid, actor=user))
            if len(rest) == 4 and rest[0] == "projections" and rest[2] == "runs":
                return self._send(200, platform.data.get_projection_run(
                    wid, rest[1], rest[3], user))
            if rest == ["capabilities"]:
                return self._send(200, platform.capabilities.list(wid, actor=user))
            if rest == ["applications"]:
                return self._send(200, platform.applications.list(wid, actor=user))
            if rest == ["releases"]:
                return self._send(200, platform.releases.list(wid, user))
            if rest == ["metrics"]:
                return self._send(200, platform.metrics(
                    wid, user, window_seconds=self._window_seconds(query)))
            if rest == ["slo"]:
                return self._send(200, platform.service_levels(
                    wid, user,
                    window_seconds=self._window_seconds(query)))
            if rest == ["events"]:
                return self._send(200, platform.events.list(
                    wid, limit=self._query_int(query, "limit", 200),
                    event_type=query.get("type")))
            if rest == ["events", "dead-letters"]:
                return self._send(200, platform.events.dead_letters(wid))
            if rest == ["executions"]:
                from ..application.execution_ledger import ExecutionLedger

                return self._send(200, ExecutionLedger(platform.ctx).list(
                    wid, user))
            if rest == ["agent-runs"]:
                platform.ctx.require(user, wid, "viewer")
                return self._send(200, platform.ctx.repo.list(
                    "agent_run", wid))
            if len(rest) == 2 and rest[0] == "agent-runs":
                platform.ctx.require(user, wid, "viewer")
                run = platform.ctx.repo.get("agent_run", wid, rest[1])
                if run is None:
                    return self._send(404, {"error": "agent run not found"})
                steps = [s for s in platform.ctx.repo.list("agent_step", wid)
                         if s.get("run_id") == rest[1]]
                return self._send(200, {**run, "steps": sorted(
                    steps, key=lambda s: s.get("index", 0))})
            if rest and rest[0] == "executions" and len(rest) == 3 \
                    and rest[1] == "by-key":
                from ..application.execution_ledger import (
                    ExecutionLedger, record_id_for)

                ledger = ExecutionLedger(platform.ctx)
                record = ledger.find(wid, record_id_for(rest[2]))
                if record is None:
                    raise NotFoundError(
                        f"execution not found for key: {rest[2]!r}")
                platform.ctx.require(user, wid, "viewer")
                return self._send(200, record)
            if rest and rest[0] == "executions" and len(rest) == 2:
                from ..application.execution_ledger import ExecutionLedger

                record = ExecutionLedger(platform.ctx).find(wid, rest[1])
                if record is None:
                    raise NotFoundError(f"execution not found: {rest[1]!r}")
                platform.ctx.require(user, wid, "viewer")
                return self._send(200, record)
            if rest == ["verify"]:
                return self._send(200, platform.verifier.verify_workspace(
                    wid, user, object_type=query.get("object_type"),
                    sample=self._query_int(query, "sample", 0) or None))
            if rest == ["reconcile"]:
                # read-only: reconciliation emits an audit event only when an
                # operator explicitly asks for a cycle (P1-7)
                return self._send(200, platform.reconciler.reconcile(
                    wid, user, emit=False))
            if rest == ["canary"]:
                from ..application.releases import canary_status

                platform.ctx.require(user, wid, "viewer")
                return self._send(200, {
                    **canary_status(platform.ctx, wid),
                    "check": platform.releases.canary_check(wid, user),
                })
            if rest == ["controller", "propose"]:
                return self._send(200, platform.controller.propose(wid, user))
            if rest and rest[0] == "applications" and len(rest) == 3 \
                    and rest[2] == "objective":
                # GET is a read: evaluation is POST .../objective/evaluate
                return self._send(200, platform.objectives.get(
                    wid, rest[1], user))
            if rest and rest[0] == "applications" and len(rest) == 2:
                return self._send(200, platform.applications.get(wid, rest[1], actor=user))
            if rest == ["todos"]:
                return self._send(200, platform.objectives.list_todos(
                    wid, user, status=query.get("status", "open")))
            if rest == ["state", "find"]:
                object_type = query.get("type")
                if not object_type:
                    raise ValidationError("query param 'type' is required")
                reserved = {"type", "user", "report", "limit", "offset"}
                report = str(query.get("report", "")).lower() in {"1", "true", "yes"}
                limit = self._query_int(query, "limit", 0)
                offset = max(0, self._query_int(query, "offset", 0))
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
                result = gateway_of(platform, wid).find_objects(
                    object_type, filters, report=report)
                # pagination (plan-v2 1.9): the kernel returns the full page;
                # the API exposes stable limit/offset slicing plus totals
                if isinstance(result, dict):
                    objects = result.get("objects") or []
                    total = len(objects)
                    sliced = objects[offset:offset + limit] if limit else objects[offset:]
                    return self._send(200, {
                        **result, "objects": sliced, "total": total,
                        "limit": limit, "offset": offset,
                    })
                if limit or offset:
                    total = len(result)
                    return self._send(200, {
                        "objects": result[offset:offset + limit] if limit
                        else result[offset:],
                        "total": total, "limit": limit, "offset": offset,
                    })
                return self._send(200, result)
            if rest == ["state", "get"]:
                return self._send(200, gateway_of(platform, wid).get_object(query.get("ref", "")))
            if rest == ["state", "related"]:
                return self._send(200, gateway_of(platform, wid).related(
                    query.get("ref", ""), query.get("relation", ""),
                    direction=query.get("direction", "out")))
            if rest == ["state", "history"]:
                return self._send(200, platform.history.changes_for(
                    wid, user, query.get("ref", ""),
                    limit=self._query_int(query, "limit", 50)))
            if rest == ["state", "as-of"]:
                ref = query.get("ref", "")
                at = query.get("at")
                if at:
                    return self._send(200, platform.history.object_at_time(
                        wid, user, ref, at))
                seq = self._query_int(query, "seq", 0)
                if seq <= 0:
                    raise ValidationError(
                        "query param 'seq' (positive integer) or 'at' "
                        "(RFC3339 timestamp) is required")
                return self._send(200, platform.history.object_as_of(
                    wid, user, ref, seq))
            if rest == ["changes"]:
                changes = gateway_of(platform, wid).observe_changes(
                    self._query_int(query, "after", 0))
                limit = self._query_int(query, "limit", 0)
                return self._send(200, changes[-limit:] if limit else changes)
            if rest == ["audit"]:
                return self._send(200, platform.ctx.audit.list(
                    wid, limit=self._query_int(query, "limit", 50)))
            raise ValidationError(f"no route for {self.path}")

        # ------------------------------------------------------------ POST

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            try:
                body = self._body()
                if parts == ["api", "llm"]:
                    return self._post_llm(body)
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

        def _post_llm(self, body: dict) -> None:
            """Configure the platform LLM once, server-side (v0.8).

            Per-request provider/credential injection is forbidden; the
            console saves the choice here instead of shipping an API key in
            every draft/agent request.
            """
            self._actor(body=body)
            provider = _provider_from(body, allow_openai=True)
            if provider is None:
                raise ValidationError(
                    "llm config needs {'provider': 'openai', ...} or "
                    "a demo script"
                )
            platform.configure_llm(provider)
            return self._send(200, {
                "ok": True,
                "llm_provider": type(provider).__name__,
            })

        def _post(self, wid: str | None, rest: list[str], body: dict) -> None:
            user = self._actor(body=body)
            if wid is None:
                if rest == []:
                    return self._send(200, platform.workspaces.create(
                        body.get("name", ""), actor=user,
                        description=body.get("description", "")))
                raise ValidationError("no route")
            if rest == ["members"]:
                return self._send(200, platform.workspaces.add_member(
                    wid, body.get("member") or body["user_id"], body["role"],
                    actor=user))
            if rest == ["llm-config"]:
                return self._send(200, platform.llm_config.put(wid, user, body))
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.create(
                    wid, body.get("name", ""), actor=user,
                    description=body.get("description", ""),
                    profile=body.get("profile", "closed")))
            if rest == ["ontology", "objects"]:
                return self._send(200, platform.ontologies.upsert_object(
                    wid, user, body["name"], properties=body.get("properties", []),
                    description=body.get("description", ""),
                    identity=body.get("identity"),
                    category=body.get("category", "object"),
                    state_machine=body.get("state_machine"),
                    expected_version=self._expected_version(body)))
            if rest == ["ontology", "relations"]:
                return self._send(200, platform.ontologies.upsert_relation(
                    wid, user, body["name"], body["source"], body["target"],
                    description=body.get("description", ""),
                    cardinality=body.get("cardinality", ""),
                    inverse=body.get("inverse", ""),
                    expected_version=self._expected_version(body)))
            if rest == ["ontology", "vocabulary"]:
                return self._send(200, platform.ontologies.add_term(
                    wid, user, body["term"], body["concept"],
                    kind=body.get("kind", "object"), aliases=body.get("aliases"),
                    expected_version=self._expected_version(body)))
            if rest == ["ontology", "draft"]:
                return self._send(200, platform.ontologies.draft(
                    wid, user, body.get("text", ""),
                    apply=bool(body.get("apply", False)),
                    provider=_provider_from(body),
                    name=body.get("name", ""),
                    draft=body.get("draft")))
            if rest == ["ontology", "validate"]:
                problems = platform.ontologies.validate(
                    wid, user, expected_version=self._expected_version(body))
                return self._send(200, {"ok": not problems, "problems": problems})
            if rest == ["ontology", "revisions"]:
                return self._send(200, platform.ontologies.revise(wid, user))
            if rest == ["ontology", "publish"]:
                return self._send(200, platform.ontologies.publish(
                    wid, user, expected_version=self._expected_version(body),
                    allow_breaking=bool(body.get("allow_breaking"))))
            if rest == ["ontology", "lint"]:
                return self._send(200, platform.ontologies.lint(wid, user))
            if rest == ["datasources"]:
                return self._send(200, platform.data.connect_source(
                    wid, user, body["name"], body.get("kind", "static"),
                    config=body.get("config", {}), assets=body.get("assets", [])))
            if rest == ["projections"]:
                return self._send(200, platform.data.create_projection(
                    wid, user, body["name"], body["datasource_id"],
                    object_mappings=body.get("object_mappings", []),
                    relation_mappings=body.get("relation_mappings", []),
                    row_limit=int(body.get("row_limit") or 0),
                    mode=str(body.get("mode") or "additive")))
            if len(rest) == 3 and rest[0] == "projections" and rest[2] == "run":
                return self._send(200, platform.data.run_projection(wid, rest[1], user))
            if rest == ["capabilities"]:
                return self._send(200, platform.capabilities.create(
                    wid, user, key=body["key"], name=body["name"],
                    target_type=body["target_type"],
                    description=body.get("description", ""),
                    inputs=body.get("inputs", []),
                    implementation=body.get("implementation"),
                    contract=body.get("contract")))
            if len(rest) == 3 and rest[0] == "capabilities" and rest[2] == "publish":
                return self._send(200, platform.capabilities.publish(wid, rest[1], user))
            if rest == ["releases"]:
                return self._send(200, platform.releases.cut(
                    wid, user, note=body.get("note", "")))
            if len(rest) == 3 and rest[0] == "releases" and rest[2] == "deploy":
                return self._send(200, platform.releases.deploy(wid, user, rest[1]))
            if len(rest) == 3 and rest[0] == "releases" and rest[2] == "canary":
                return self._send(200, platform.releases.canary(wid, user, rest[1]))
            if rest == ["releases", "canary", "check"]:
                return self._send(200, platform.releases.check_canary(
                    wid, user))
            if rest == ["releases", "canary", "promote"]:
                return self._send(200, platform.releases.promote_canary(
                    wid, user, require_healthy=bool(
                        body.get("require_healthy", True))))
            if rest == ["releases", "rollback"]:
                return self._send(200, platform.releases.rollback(
                    wid, user, to_release_id=body.get("release_id")))
            if rest == ["events", "dispatch"]:
                return self._send(200, platform.dispatcher.dispatch(wid))
            if rest == ["controller", "act"]:
                return self._send(200, platform.controller.act(
                    wid, user, accept=bool(body.get("accept", True))))
            if rest == ["invoke"]:
                result = platform.capabilities.invoke(
                    wid, user, body["function"], target=body.get("target"),
                    args=body.get("args", {}),
                    idempotency_key=(
                        self.headers.get("Idempotency-Key")
                        or body.get("idempotency_key")),
                    timeout_seconds=(
                        float(body["timeout"])
                        if body.get("timeout") is not None else None))
                status = 202 if result.get("status") == "unknown" else 200
                return self._send(status, result)
            if rest == ["applications"]:
                return self._send(200, platform.applications.create(
                    wid, user, body["name"], description=body.get("description", "")))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "publish":
                return self._send(200, platform.applications.publish(wid, rest[1], user))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "objective":
                return self._send(200, platform.objectives.set(
                    wid, rest[1], user, body["metric"], target=body["target"],
                    band=body.get("band", 0.0),
                    window=int(body.get("window", 3600))))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "objective" and rest[3] == "evaluate":
                return self._send(200, platform.objectives.evaluate(
                    wid, rest[1], user))
            if len(rest) == 3 and rest[0] == "executions" and rest[2] == "resolve":
                from ..application.execution_ledger import ExecutionLedger

                return self._send(200, ExecutionLedger(platform.ctx).resolve(
                    wid, rest[1], user, body.get("outcome", ""),
                    note=body.get("note", "")))
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
            if len(rest) == 3 and rest[0] == "todos" and rest[2] == "resolve":
                return self._send(200, platform.objectives.resolve_todo(
                    wid, user, rest[1], note=body.get("note", "")))
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


def _provider_from(body: dict, allow_openai: bool = False):
    """Build a provider from a request body.

    Request-scoped OpenAI provider/credential injection is refused (v0.8):
    the key would otherwise travel through every draft/agent call and be
    visible to any client.  Configure it once via ``POST /api/llm`` or the
    ``ONTO_LLM_*`` environment instead.  A scripted echo provider is still
    accepted - it carries no credentials and is what the demo mode and the
    HTTP contract tests use.
    """
    llm = body.get("llm") or {}
    if not llm and "provider" in body:  # POST /api/llm direct payload
        llm = body
    if llm.get("provider") == "openai":
        if not allow_openai:
            raise ValidationError(
                "request-scoped LLM provider injection is disabled; "
                "configure the platform LLM via POST /api/llm or ONTO_LLM_*"
            )
        return OpenAICompatProvider(
            base_url=llm.get("base_url", "https://api.openai.com/v1"),
            model=llm.get("model", "gpt-4o-mini"),
            api_key=llm.get("api_key", ""),
        )
    if "script" in llm:
        return EchoLLMProvider(list(llm.get("script", [])))
    return None


def serve(platform: Platform, host: str = "127.0.0.1", port: int = 8100) -> None:
    if host not in ("127.0.0.1", "localhost", "::1") \
            and not os.environ.get("ONTO_API_TOKEN"):
        raise ValidationError(
            "refusing non-loopback bind without ONTO_API_TOKEN: local "
            "single-user mode is loopback-only")
    server = ThreadingHTTPServer((host, port), build_handler(platform))
    platform.start_scheduler()
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
    finally:
        server.server_close()
        platform.close()


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

    The implementation lives in ``infrastructure/llm/factory.py`` so the CLI
    and the HTTP adapter share one environment contract.
    """
    from ..infrastructure.llm.factory import provider_from_env as build

    return build()


if __name__ == "__main__":  # pragma: no cover
    main()
