"""REST API adapter (stdlib only).

All routes live under /api; the web console is served at /.
Design-time resources and runtime state are exposed through the same
use cases the CLI uses - no domain logic here.

    GET  /api/health
    GET  /api/workspaces                        POST /api/workspaces
    GET  /api/workspaces/{wid}
    POST /api/workspaces/{wid}/delete          {confirm: true}  (empty only)
    POST /api/workspaces/{wid}/reset           {confirm: true, confirm_name}
    GET  /api/workspaces/{wid}/ontology         POST (same)  {name}
    PATCH /api/workspaces/{wid}/ontology        {name?, description?, profile?}
    POST /api/workspaces/{wid}/ontology/discard (drop draft, restore published)
    POST /api/workspaces/{wid}/ontology/objects {name, properties, description}
    POST /api/workspaces/{wid}/ontology/relations
    POST /api/workspaces/{wid}/ontology/vocabulary
    DELETE /api/workspaces/{wid}/ontology/objects/{name}
    DELETE /api/workspaces/{wid}/ontology/relations/{name}
    DELETE /api/workspaces/{wid}/ontology/vocabulary/{term}
    POST /api/workspaces/{wid}/ontology/validate | /publish | /lint | /revisions
    GET  /api/workspaces/{wid}/ontology/workflow?round_id=
    POST /api/workspaces/{wid}/ontology/workflow/rounds
    GET  /api/workspaces/{wid}/ontology/workflow/rounds/{rid}
    PATCH/DELETE /ontology/workflow/rounds/{rid}[/cqs|reuse|samples/{item}]
    POST /api/workspaces/{wid}/ontology/workflow/rounds/{rid}/check|bind|next
    POST .../rounds/{rid}/cqs|reuse|samples|runs|reviews|publish
    GET  .../rounds/{rid}/trace-matrix|publish-check|report
    GET  /api/workspaces/{wid}/state/history?ref=&limit=
    GET  /api/workspaces/{wid}/state/as-of?ref=&seq=   (or &at=RFC3339)
    GET  /api/workspaces/{wid}/runtime          (kernel describe)
    GET  /api/workspaces/{wid}/datasources      POST (same)
    PATCH /api/workspaces/{wid}/datasources/{sid}  {name?, config?, assets?, test_connection?}
    POST /api/workspaces/{wid}/datasources/{sid}/test
    POST /api/workspaces/{wid}/datasources/{sid}/preview {asset, limit?}
    DELETE /api/workspaces/{wid}/datasources/{sid}
    GET  /api/workspaces/{wid}/projections      POST (same)
    PATCH /api/workspaces/{wid}/projections/{pid}  {name?, object_mappings?, ...}
    DELETE /api/workspaces/{wid}/projections/{pid}
    POST /api/workspaces/{wid}/projections/{pid}/run
    GET  /api/workspaces/{wid}/capabilities     POST (same)
    PATCH /api/workspaces/{wid}/capabilities/{cid}  {key?, name?, target_type?, ...} (draft only)
    POST /api/workspaces/{wid}/capabilities/{cid}/clone {key?, name?}
    DELETE /api/workspaces/{wid}/capabilities/{cid}
    POST /api/workspaces/{wid}/capabilities/{cid}/publish
    POST /api/workspaces/{wid}/invoke           {function, target, args}
    GET  /api/workspaces/{wid}/releases         POST (same) {note}
    POST /api/workspaces/{wid}/releases/{rid}/deploy
    POST /api/workspaces/{wid}/releases/{rid}/canary
    POST /api/workspaces/{wid}/releases/canary/promote {require_healthy?}
    POST /api/workspaces/{wid}/releases/rollback {release_id?}
    GET  /api/workspaces/{wid}/events?limit=200&type=
    POST /api/workspaces/{wid}/events/dispatch
    # P1 rules / business events / application spec
    GET  /api/workspaces/{wid}/rules?lifecycle=        {"rules":[...]}
    POST /api/workspaces/{wid}/rules                   {name,object_type,condition,...}
    POST /api/workspaces/{wid}/rules/{rid}/publish
    POST /api/workspaces/{wid}/rules/evaluate          {object_type?|refs?}
    GET  /api/workspaces/{wid}/event-contracts?lifecycle=   {"contracts":[...]}
    POST /api/workspaces/{wid}/event-contracts         {name,aggregate_kind,payload_schema,...}
    POST /api/workspaces/{wid}/event-contracts/{name}/publish
    GET  /api/workspaces/{wid}/event-subscriptions     {"subscriptions":[...]}
    POST /api/workspaces/{wid}/event-subscriptions     {name,event_type,filter?,handler_key,...}
    DELETE /api/workspaces/{wid}/event-subscriptions/{sid}
    POST /api/workspaces/{wid}/events/emit             {event_type,aggregate_kind,aggregate_id,payload}
    GET  /api/workspaces/{wid}/applications/{aid}/spec
    POST /api/workspaces/{wid}/applications/{aid}/interactions
    DELETE /api/workspaces/{wid}/applications/{aid}/interactions/{iid}
    GET  /api/workspaces/{wid}/applications     POST (same)
    PATCH /api/workspaces/{wid}/applications/{aid}  {name?,description?}
    PATCH /api/workspaces/{wid}/applications/{aid}/views/{alias}
    PATCH /api/workspaces/{wid}/applications/{aid}/queries/{alias}
    POST /api/workspaces/{wid}/applications/{aid}/clone  {name?}
    DELETE /api/workspaces/{wid}/applications/{aid}
    DELETE /api/workspaces/{wid}/applications/{aid}/views/{alias}
    DELETE /api/workspaces/{wid}/applications/{aid}/queries/{alias}
    DELETE /api/workspaces/{wid}/applications/{aid}/agents/{alias}
    DELETE /api/workspaces/{wid}/applications/{aid}/capabilities/{cid}
    POST /api/workspaces/{wid}/applications/{aid}/publish
    POST /api/workspaces/{wid}/applications/{aid}/query  {alias}
    POST /api/workspaces/{wid}/applications/{aid}/objective {metric,target,band,window}
    GET  /api/workspaces/{wid}/applications/{aid}/objective   (current/deviation/trend)
    GET  /api/workspaces/{wid}/todos?status=open
    POST /api/workspaces/{wid}/todos/{tid}/resolve
    GET  /api/workspaces/{wid}/state/find?type=X&prop__op=v
    GET  /api/workspaces/{wid}/state/get?ref=Type/Id
    GET  /api/workspaces/{wid}/state/related?ref=&relation=&direction=
    DELETE /api/workspaces/{wid}/state/objects?ref=Type/Id
    PATCH  /api/workspaces/{wid}/state/objects?ref=Type/Id {properties}
    GET  /api/workspaces/{wid}/changes?after=0
    POST /api/workspaces/{wid}/ask              {question}
    POST /api/workspaces/{wid}/agent            {goal}
    GET  /api/workspaces/{wid}/smart-ui/meta
    POST /api/workspaces/{wid}/smart-ui/compile {question, roles}
    POST /api/workspaces/{wid}/smart-ui/actions/{action_id}
    GET  /api/workspaces/{wid}/security/policies
    POST /api/workspaces/{wid}/security/policies
    PUT  /api/workspaces/{wid}/security/policies/{id}
    GET  /api/workspaces/{wid}/security/policies/{id}
    GET  /api/workspaces/{wid}/security/policies/{id}/diff
    POST /api/workspaces/{wid}/security/policies/{id}/validate
    POST /api/workspaces/{wid}/security/policies/{id}/dry-run
    POST /api/workspaces/{wid}/security/policies/{id}/approve
    POST /api/workspaces/{wid}/security/policies/{id}/publish
    POST /api/workspaces/{wid}/security/policies/{id}/rollback
    POST /api/workspaces/{wid}/security/authorize
    POST /api/workspaces/{wid}/security/authorize/preview
    POST /api/workspaces/{wid}/security/blueprint/validate
    GET  /api/workspaces/{wid}/security/roles
    GET  /api/workspaces/{wid}/security/scopes
    GET  /api/workspaces/{wid}/security/bindings
    POST /api/workspaces/{wid}/security/bindings
    DELETE /api/workspaces/{wid}/security/bindings/{id}
    GET  /api/workspaces/{wid}/security/groups
    POST /api/workspaces/{wid}/security/groups
    GET  /api/workspaces/{wid}/security/relations
    POST /api/workspaces/{wid}/security/relations
    POST /api/workspaces/{wid}/security/relations/delete
    GET  /api/workspaces/{wid}/security/decisions?limit=
    GET  /api/workspaces/{wid}/security/decisions/{id}
    GET  /api/workspaces/{wid}/security/decisions/export?fmt=jsonl
    POST /api/workspaces/{wid}/security/policies/draft   {prompt, mode}
    GET  /api/workspaces/{wid}/security/org/tree
    POST /api/workspaces/{wid}/security/org/units
    POST /api/workspaces/{wid}/security/org/units/{id}/members
    GET  /api/workspaces/{wid}/security/identity/users
    POST /api/workspaces/{wid}/security/identity/users
    POST /api/workspaces/{wid}/security/identity/login/oidc  {id_token}
    POST /api/workspaces/{wid}/security/identity/login/ldap  {username,password}
    GET  /api/workspaces/{wid}/security/identity/session     (Bearer session)
    GET  /api/workspaces/{wid}/security/webhooks
    POST /api/workspaces/{wid}/security/webhooks
    POST /api/workspaces/{wid}/security/async/tasks
    GET  /api/workspaces/{wid}/security/async/tasks
    GET  /api/workspaces/{wid}/state/export?type=X&fmt=json|csv
    GET  /api/workspaces/{wid}/audit
    GET|POST /scim/v2/Users                              (SCIM bearer token)
    GET  /smart-ui/                             (separate OpenUI runtime build)
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..bootstrap import Platform, PlatformConfig, build_platform
from ..application.security import context as auth_context
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
from ..application.smartui.packs import (
    set_workspace_pack,
    workspace_pack_id,
    workspace_rule_pack_id,
)
from ..application.smartui.rules import (
    archive_rule_pack,
    bind_rule_pack,
    clone_rule_pack,
    create_rule_pack,
    dry_run_rule_pack,
    get_rule_pack,
    list_rule_packs,
    publish_rule_pack,
    rollback_rule_pack,
    update_rule_pack,
    validate_rule_pack,
)
from ..infrastructure.llm.echo import EchoLLMProvider
from ..infrastructure.llm.openai_compat import OpenAICompatProvider
from .smart_ui import (
    compile_smart_ui,
    invoke_smart_action,
    smart_ui_engine_available,
    smart_ui_focus_options,
    smart_ui_meta,
)
from .smart_ui_studio import (
    accept_generation,
    application_ui_view,
    cancel_generation,
    delete_draft,
    generation_view,
    get_generation,
    list_drafts,
    list_generations,
    delete_generation,
    publish_generation,
    refine_generation,
    replay_generation,
    start_generation,
    studio_enabled,
    update_draft,
)

import hmac
import os

WEB_DIR = Path(__file__).resolve().parent / "web"
# The React console is built into ``web/dist`` (see frontend/). Serving it from
# the package keeps ``python -m onto_platform.interfaces.api`` the one-command
# startup: no Node, no separate dev server, no second process at runtime.
DIST_DIR = WEB_DIR / "dist"
# The smart-UI runtime is a separate Vite build (vibe-可视化engine/runtime-web).
# It lives next to the console instead of inside ``dist`` so rebuilding the
# console does not delete it.
SMART_UI_DIR = WEB_DIR / "smart-ui"

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

        def _origin(self) -> str | None:
            origin = (self.headers.get("Origin") or "").strip()
            return origin or None

        def _cors_origins(self) -> set[str]:
            """Explicit cross-origin allowlist (config beats env only by union).

            Sources: ``platform.config.extra["cors_origins"]`` (list or comma
            separated string) and the ``ONTO_CORS_ORIGINS`` environment
            variable.  Matching is exact on scheme+host+port.
            """
            raw = platform.config.extra.get("cors_origins") or []
            if isinstance(raw, str):
                raw = raw.split(",")
            origins = {str(item).strip().rstrip("/") for item in raw
                       if str(item).strip()}
            env = os.environ.get("ONTO_CORS_ORIGINS", "")
            origins.update(item.strip().rstrip("/") for item in env.split(",")
                           if item.strip())
            return origins

        def _origin_allowed(self, origin: str) -> bool:
            host = (self.headers.get("Host") or "").strip()
            netloc = urlparse(origin).netloc
            if host and netloc and netloc.lower() == host.lower():
                return True  # the console served by this very host
            return origin.rstrip("/") in self._cors_origins()

        def _check_origin(self) -> None:
            """Refuse a cross-site request whose Origin is not allowlisted.

            Applies to every method (including OPTIONS).  No CORS grant is
            emitted for a denied request, so a browser cannot read the 403
            response either.
            """
            origin = self._origin()
            if origin and not self._origin_allowed(origin):
                raise AccessDeniedError(
                    f"cross-site request blocked: origin {origin!r} is not "
                    f"allowed")

        def _cors(self) -> None:
            """CORS headers for an allowed request.

            No Origin (local console/curl) keeps working without an
            ``Access-Control-Allow-Origin`` header; a specific allowed Origin
            is echoed back (never ``*``) and varies by Origin.
            """
            origin = self._origin()
            if origin:
                if not self._origin_allowed(origin):
                    return
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, PUT, PATCH, DELETE, OPTIONS")
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

        def _send_bytes(self, status: int, content_type: str, body: bytes,
                        filename: str = "",
                        headers: dict[str, str] | None = None) -> None:
            # Text formats get an explicit charset so browsers/CLI clients do
            # not have to guess how to decode UTF-8 Turtle/CSV bodies.  Other
            # call sites (JSON, binary exports) keep their exact media type.
            if (content_type.startswith("text/")
                    and "charset=" not in content_type.lower()):
                content_type = f"{content_type}; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if filename:
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{filename}"')
            for key, value in (headers or {}).items():
                self.send_header(str(key), str(value))
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
            # CSRF hardening: a cross-site form can send text/plain (or a
            # simple content type) without a preflight, so a state-changing
            # JSON request must prove its content type.
            raw_type = (self.headers.get("Content-Type") or "").split(";")[0]
            if raw_type.strip().lower() != "application/json":
                raise ValidationError(
                    "POST, PUT and PATCH require Content-Type: application/json")
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValidationError("request body must be a JSON object")
            return data

        def _bearer(self) -> str:
            header = (self.headers.get("Authorization") or "").strip()
            if header.lower().startswith("bearer "):
                return header[7:].strip()
            return ""

        def _session_actor(self, token: str) -> str:
            """Map a signed OIDC/LDAP session token to a directory user.

            Session tokens are only minted after a successful OIDC/LDAP login
            and are signed with ``ONTO_SESSION_SECRET``; an invalid, expired or
            unknown token yields an empty string so the caller can return the
            normal 401 instead of leaking which check failed.
            """
            identity = getattr(platform, "identity", None)
            if identity is None or not token:
                return ""
            try:
                user = identity.resolve_session(token)
            except AccessDeniedError:
                return ""
            if not user.get("active", True):
                return ""
            return str(user.get("user_name") or user.get("id") or "")

        def _actor(self, body: dict | None = None, query: dict | None = None) -> str:
            """Resolve the actor of one request.

            Accepted credentials, in order:

            1. the configured ``ONTO_API_TOKEN`` bearer token (machine token),
            2. a signed session token issued by ``/identity/login/oidc`` or
               ``/identity/login/ldap``, resolved through the identity
               directory (deactivated users fail closed),
            3. loopback local mode without any bearer token, where the declared
               ``user`` is accepted for developer ergonomics.

            A supplied but invalid bearer token is always rejected; it never
            silently falls back to the local admin identity.
            """
            expected = os.environ.get("ONTO_API_TOKEN")
            token = self._bearer()
            if token:
                if expected and hmac.compare_digest(token, expected):
                    return os.environ.get("ONTO_API_TOKEN_ACTOR",
                                          "api-client")
                session_actor = self._session_actor(token)
                if session_actor:
                    return session_actor
                raise AccessDeniedError("invalid or expired bearer token")
            if expected:
                raise AccessDeniedError("invalid or missing bearer token")
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

        def _required_version(self, body: dict) -> int:
            """Mutating policy routes must carry an optimistic-lock token.

            Service-level callers may omit it, but the HTTP contract does not:
            two operators publishing the same draft must not overwrite each
            other silently.
            """
            value = self._expected_version(body)
            if value is None:
                raise ValidationError(
                    "expected_version or If-Match is required for this policy "
                    "mutation")
            return value

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
            origin = self._origin()
            if origin and not self._origin_allowed(origin):
                return self._error(AccessDeniedError(
                    f"cross-site request blocked: origin {origin!r} is not "
                    f"allowed"))
            # a preflight without Origin (local tooling) stays usable and
            # advertises the mutating methods, PUT/PATCH included
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()

        # ------------------------------------------------------------ DELETE

        def do_DELETE(self) -> None:
            auth_context.clear()
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            query = {k: _parse_scalar(v[-1]) for k, v in parse_qs(parsed.query).items()}
            try:
                self._check_origin()
                if parts[:2] == ["scim", "v2"]:
                    platform.scim.authenticate(
                        self.headers.get("Authorization") or "")
                    if parts[2:3] == ["Groups"] and len(parts) == 4:
                        return self._send(200, platform.scim.delete_group(
                            parts[3]))
                    raise ValidationError(f"no SCIM route for {self.path}")
                if parts[:2] != ["api", "workspaces"]:
                    raise ValidationError(f"no route for {self.path}")
                wid = parts[2] if len(parts) > 2 else None
                rest = parts[3:]
                return self._delete(wid, rest, query)
            except PlatformError as exc:
                return self._error(exc)
            except (KeyError, json.JSONDecodeError) as exc:
                return self._error(ValidationError(f"invalid request: {exc}"))
            except Exception as exc:  # pragma: no cover
                return self._error(exc)

        def _delete(self, wid: str | None, rest: list[str],
                    query: dict) -> None:
            user = self._actor(query=query)
            if wid is None:
                raise ValidationError("workspace id is required")
            if rest[:2] == ["ontology", "workflow"]:
                return self._workflow_delete(wid, rest[2:], query)
            if len(rest) == 3 and rest[0] == "security" \
                    and rest[1] == "bindings":
                return self._send(200, platform.security.delete_binding(
                    wid, user, rest[2]))
            if len(rest) == 3 and rest[0] == "security" \
                    and rest[1] == "groups":
                return self._send(200, platform.security.delete_group(
                    wid, user, rest[2]))
            if len(rest) == 3 and rest[0] == "security" \
                    and rest[1] == "org" and rest[2] == "units":
                raise ValidationError("organization unit id is required")
            if len(rest) == 4 and rest[0] == "security" \
                    and rest[1] == "org" and rest[2] == "units":
                return self._send(200, platform.organization.delete_unit(
                    wid, user, rest[3],
                    cascade=str(query.get("cascade") or "") in
                    {"1", "true", "yes"}))
            if len(rest) == 3 and rest[0] == "security" \
                    and rest[1] == "identity" and rest[2] == "users":
                raise ValidationError("identity user id is required")
            if len(rest) == 4 and rest[0] == "security" \
                    and rest[1] == "identity" and rest[2] == "users":
                platform.ctx.require_action(user, wid, "security.manage")
                return self._send(200, platform.identity_directory.delete_user(
                    rest[3], actor=user))
            if len(rest) == 3 and rest[0] == "security" \
                    and rest[1] == "webhooks":
                return self._send(200, platform.webhooks.delete_webhook(
                    wid, user, rest[2]))
            if len(rest) == 4 and rest[0] == "security" \
                    and rest[1] == "webhooks" and rest[3] == "delete":
                return self._send(200, platform.webhooks.delete_webhook(
                    wid, user, rest[2]))
            if len(rest) == 2 and rest[0] == "members":
                return self._send(200, platform.workspaces.remove_member(
                    wid, rest[1], actor=user))
            if len(rest) == 3 and rest[0] == "ontology" and rest[1] == "objects":
                return self._send(200, platform.ontologies.remove_object(
                    wid, user, rest[2]))
            if len(rest) == 3 and rest[0] == "ontology" and rest[1] == "relations":
                return self._send(200, platform.ontologies.remove_relation(
                    wid, user, rest[2]))
            if len(rest) == 3 and rest[0] == "ontology" and rest[1] == "vocabulary":
                return self._send(200, platform.ontologies.remove_term(
                    wid, user, rest[2]))
            if rest == ["state", "objects"]:
                ref = query.get("ref")
                if not ref:
                    raise ValidationError("query param 'ref' is required")
                result = platform.state.delete_objects(wid, user, [str(ref)])
                if not result["deleted"]:
                    raise NotFoundError(f"object not found: {ref}")
                return self._send(200, result)
            if len(rest) == 2 and rest[0] == "capabilities":
                return self._send(200, platform.capabilities.delete(
                    wid, rest[1], user))
            if len(rest) == 2 and rest[0] == "datasources":
                return self._send(200, platform.data.delete_source(
                    wid, user, rest[1]))
            if len(rest) == 2 and rest[0] == "projections":
                return self._send(200, platform.data.delete_projection(
                    wid, user, rest[1]))
            if len(rest) == 2 and rest[0] == "applications":
                return self._send(200, platform.applications.delete(
                    wid, rest[1], user))
            if len(rest) == 2 and rest[0] == "event-subscriptions":
                return self._send(200,
                                  platform.business_events.delete_subscription(
                                      wid, user, rest[1]))
            if len(rest) == 3 and rest[:2] == ["smart-ui", "generations"]:
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                return self._send(200, delete_generation(
                    platform, wid, user, rest[2]))
            if len(rest) == 3 and rest[:2] == ["smart-ui", "drafts"]:
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                return self._send(200, delete_draft(platform, wid, user, rest[2]))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "views":
                return self._send(200, platform.applications.remove_view(
                    wid, rest[1], rest[3], user))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "queries":
                return self._send(200, platform.applications.remove_query(
                    wid, rest[1], rest[3], user))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "agents":
                return self._send(200, platform.applications.remove_agent(
                    wid, rest[1], rest[3], user))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "capabilities":
                return self._send(200, platform.applications.detach_capability(
                    wid, rest[1], rest[3], user))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "interactions":
                return self._send(200, platform.applications.remove_interaction(
                    wid, rest[1], rest[3], user))
            raise ValidationError(f"no route for {self.path}")

        # ------------------------------------------------------------ GET

        def do_GET(self) -> None:
            auth_context.clear()
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            query = {k: _parse_scalar(v[-1]) for k, v in parse_qs(parsed.query).items()}
            try:
                # Static console/runtime assets are public build output: they are
                # served before the origin gate so the sandboxed smart-UI iframe
                # (opaque origin -> ``Origin: null``) can load its module graph.
                # API calls stay behind the origin allowlist below; the iframe
                # reaches them through the console bridge, never directly.
                if not parts or parts[0] not in ("api", "scim"):
                    return self._serve_web(parts)
                self._check_origin()
                if parts == ["api", "health"]:
                    provider = platform.ctx.llm
                    engine_ready = smart_ui_engine_available()
                    runtime_ready = (SMART_UI_DIR / "index.html").is_file()
                    smart_ui_features = []
                    if engine_ready:
                        smart_ui_features += ["smart-ui.bridge", "smart-ui.actions"]
                    if runtime_ready:
                        smart_ui_features.append("smart-ui.runtime")
                    if engine_ready and runtime_ready:
                        smart_ui_features.insert(0, "smart-ui")
                    if engine_ready and studio_enabled():
                        smart_ui_features.append("smart-ui.studio")
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
                            "token+session"
                            if os.environ.get("ONTO_API_TOKEN")
                            and os.environ.get("ONTO_SESSION_SECRET")
                            else "token" if os.environ.get("ONTO_API_TOKEN")
                            else "session"
                            if os.environ.get("ONTO_SESSION_SECRET")
                            else "local"),
                        "smart_ui": {
                            "engine": engine_ready,
                            "runtime": runtime_ready,
                        },
                        "features": [
                            "identity.session", "identity.oidc",
                            "identity.ldap", "identity.scim",
                            "ontology.draft", "ontology.lint", "ontology.revision",
                            "releases", "canary.isolated", "events", "metrics",
                            "slo", "slo.windowed", "verify", "reconcile",
                            "controller", "objective", "execution.ledger",
                            "execution.resolve", "scheduler", "history",
                            "agent.runs", "workspace.delete", "resource.delete",
                            *smart_ui_features,
                        ],
                    })
                if parts == ["api", "tenants"]:
                    return self._send(200, platform.tenants.list(
                        self._actor(query=query)))
                if len(parts) >= 3 and parts[:2] == ["api", "tenants"]:
                    actor = self._actor(query=query)
                    tenant_id = parts[2]
                    if len(parts) == 3:
                        return self._send(200, platform.tenants.summary(
                            tenant_id, actor))
                    if parts[3:] == ["workspaces"]:
                        return self._send(200, platform.tenants.workspaces(
                            tenant_id, actor))
                    if parts[3:] == ["members"]:
                        return self._send(200, platform.tenants.members(
                            tenant_id, actor))
                    raise ValidationError(f"no route for {self.path}")
                if parts == ["api", "scheduler"]:
                    scheduler = platform.scheduler
                    return self._send(200, scheduler.status() if scheduler
                                      else {"running": False, "tasks": []})
                if parts[:2] == ["scim", "v2"]:
                    platform.scim.authenticate(
                        self.headers.get("Authorization") or "")
                    if parts[2:3] == ["Users"]:
                        if len(parts) == 3:
                            return self._send(200, platform.scim.users(
                                filter=query.get("filter"),
                                start_index=self._query_int(query,
                                                            "startIndex", 1),
                                count=(self._query_int(query, "count", 100)
                                       if "count" in query else None)))
                        return self._send(200, platform.scim.user(parts[3]))
                    if parts[2:3] == ["Groups"]:
                        if len(parts) == 3:
                            return self._send(200, platform.scim.groups(
                                filter=query.get("filter"),
                                start_index=self._query_int(query,
                                                            "startIndex", 1),
                                count=(self._query_int(query, "count", 100)
                                       if "count" in query else None)))
                        return self._send(200, platform.scim.group(parts[3]))
                    raise ValidationError(f"no SCIM route for {self.path}")
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

            ``/smart-ui/`` is a separately built micro-frontend. It is served
            from ``web/smart-ui`` and falls back to its own ``index.html`` so a
            refreshed iframe keeps working.
            """
            if parts and parts[0] == "smart-ui":
                return self._serve_smart_ui(parts[1:])
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

        def _serve_smart_ui(self, parts: list[str]) -> None:
            root = SMART_UI_DIR.resolve()
            index = root / "index.html"
            if not index.is_file():
                return self._send(404, {
                    "error": "smart-ui runtime not built",
                    "hint": "run `npm run build:platform` in vibe-可视化engine/runtime-web",
                })
            if parts:
                candidate = (root / "/".join(parts)).resolve()
                if candidate.is_file() and candidate.is_relative_to(root):
                    return self._send_file(candidate)
                if "." in parts[-1]:
                    return self._send(404, {"error": f"smart-ui asset not found: {'/'.join(parts)}"})
            return self._send_file(index)

        def _send_file(self, path: Path) -> None:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _MIME.get(path.suffix.lower(),
                                                       "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",
                             "public, max-age=31536000, immutable"
                             if "assets" in path.parts and path.suffix != ".html"
                             else "no-cache")
            # Public build output: the smart-UI iframe has an opaque origin
            # (``Origin: null``) and module scripts are CORS requests, so the
            # grant must be wildcard rather than the console echo above.  No
            # credentials are involved and no API data goes through this path.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _get_security(self, wid: str, rest: list[str], query: dict,
                          user: str) -> None:
            """Read the permission console's policy/evidence views."""
            if rest == ["breakglass"]:
                return self._send(200, platform.break_glass.list(wid, user))
            if rest == ["mfa", "status"]:
                return self._send(200, {
                    "user": user,
                    "enrolled": platform.mfa.enrolled(user),
                })
            if rest == ["identity", "oidc", "discovery"]:
                platform.ctx.require(user, wid, "viewer")
                return self._send(200, platform.identity.oidc.discover())
            if rest == ["cache", "stats"]:
                platform.ctx.require(user, wid, "viewer")
                return self._send(200, platform.ctx.security.cache_stats())
            if rest == ["policies"]:
                return self._send(200, platform.security.policies(wid, user))
            if len(rest) == 2 and rest[0] == "policies":
                return self._send(200,
                                  platform.security.policy(wid, user, rest[1]))
            if len(rest) == 3 and rest[0] == "policies" and rest[2] == "diff":
                return self._send(200,
                                  platform.security.diff_policy(wid, user,
                                                                rest[1]))
            if rest == ["bindings"]:
                return self._send(200, platform.security.bindings(wid, user))
            if rest == ["roles"]:
                return self._send(200, platform.security.roles(wid, user))
            if rest == ["scopes"]:
                return self._send(200, platform.security.scopes(wid, user))
            if rest == ["groups"]:
                return self._send(200, platform.security.groups(wid, user))
            if rest == ["relations"]:
                return self._send(200, platform.security.relations(wid, user))
            if rest == ["decisions"]:
                limit = int(query.get("limit") or 50)
                return self._send(200, platform.security.decisions(
                    wid, user, limit=limit))
            if rest == ["decisions", "export"]:
                fmt = str(query.get("fmt") or "jsonl").lower()
                limit = int(query.get("limit") or 1000)
                result = platform.exports.export_decisions(
                    wid, user, limit=limit, fmt=fmt)
                return self._send_bytes(200, result["content_type"],
                                        result["body"],
                                        result.get("filename", ""))
            if len(rest) == 2 and rest[0] == "decisions":
                return self._send(200, platform.security.decision(
                    wid, user, rest[1]))
            if rest == ["org", "tree"]:
                platform.ctx.require_action(user, wid, "security.read")
                return self._send(200, platform.organization.tree(wid))
            if rest == ["org", "units"]:
                platform.ctx.require_action(user, wid, "security.read")
                return self._send(200, {
                    "units": platform.organization.list_units(wid)})
            if rest == ["org", "attributes"]:
                platform.ctx.require_action(user, wid, "security.read")
                who = str(query.get("actor") or user)
                return self._send(200, platform.organization.attributes_for(
                    who, wid))
            if rest == ["identity", "users"]:
                platform.ctx.require_action(user, wid, "security.read")
                manager = False
                try:
                    platform.ctx.security.check_action(
                        user, wid, "security.manage",
                        record_denials=False, record=False,
                        bind_context=False)
                    manager = True
                except AccessDeniedError:
                    pass
                users = platform.identity_directory.list_users()
                if not manager:
                    # The directory is platform-wide (one IdP can serve many
                    # workspaces).  A plain reader may only see identities
                    # that belong to this workspace, never every tenant's
                    # user names/emails/groups.
                    members = {
                        m["user"]
                        for m in platform.ctx.repo.list("membership", wid)
                    }
                    members.add(user)
                    users = [row for row in users
                             if row.get("user_name") in members]
                return self._send(200, {"users": users})
            if rest == ["identity", "session"]:
                header = (self.headers.get("Authorization") or "").strip()
                token = header[7:].strip() if header.lower().startswith(
                    "bearer ") else ""
                if not token:
                    raise AccessDeniedError("missing session token")
                return self._send(200, {
                    "user": platform.identity.resolve_session(token)})
            if rest == ["webhooks"]:
                rows = platform.webhooks.list_webhooks(wid, actor=user)
                return self._send(200, [{**row, "secret": "***"}
                                        for row in rows])
            if rest == ["async", "tasks"]:
                platform.ctx.require_action(user, wid, "security.read")
                rows = platform.async_tasks.list_tasks(wid)
                status = str(query.get("status") or "")
                if status:
                    rows = [row for row in rows
                            if row.get("status") == status]
                return self._send(200, {"tasks": rows})
            if len(rest) == 2 and rest[0] == "webhooks":
                hook = platform.webhooks.get_webhook(wid, rest[1],
                                                    actor=user)
                return self._send(200, {**hook, "secret": "***"})
            raise ValidationError("no security route")

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
            if rest[:1] == ["security"]:
                return self._get_security(wid, rest[1:], query, user)
            if rest == ["llm-config"]:
                return self._send(200, platform.llm_config.get(wid, user))
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.get(wid, actor=user))
            if rest[:2] == ["ontology", "workflow"]:
                return self._workflow_get(wid, rest[2:], query, user)
            if rest[:1] == ["standards"]:
                return self._get_standards(wid, rest[1:], query, user)
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
            if rest == ["smart-ui", "meta"]:
                meta = smart_ui_meta(platform, wid, user)
                meta["studio"] = {"enabled": studio_enabled()}
                meta["model"] = platform.llm_config.status(wid, user)
                return self._send(200, meta)
            if rest == ["smart-ui", "config"]:
                pack_id = workspace_pack_id(platform, wid, user)
                pack = None
                if pack_id:
                    try:
                        pack = platform.smartui.pack(pack_id).as_dict()
                    except Exception:
                        pack = None
                return self._send(200, {
                    "workspaceId": wid,
                    "packId": pack_id,
                    "pack": pack,
                    "rulePackId": workspace_rule_pack_id(platform, wid, user),
                })
            if rest == ["smart-ui", "rule-packs"]:
                return self._send(200, list_rule_packs(platform, wid, user))
            if (len(rest) == 3 and rest[:2] == ["smart-ui", "rule-packs"]):
                return self._send(200, get_rule_pack(platform, wid, user, rest[2]))
            if rest == ["smart-ui", "entities"]:
                return self._send(200, smart_ui_focus_options(
                    platform, wid, user,
                    object_type=str(query.get("object") or ""),
                    query=str(query.get("q") or ""),
                    limit=self._query_int(query, "limit", 20)))
            if rest == ["smart-ui", "generations"]:
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                return self._send(200, list_generations(
                    platform, wid, user,
                    limit=self._query_int(query, "limit", 30),
                    offset=self._query_int(query, "offset", 0),
                    query=str(query.get("q") or ""),
                    status=str(query.get("status") or ""),
                    object_type=str(query.get("object") or ""),
                    mode=str(query.get("mode") or "")))
            if len(rest) == 3 and rest[0] == "smart-ui" and rest[1] == "generations":
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                return self._send(200, get_generation(platform, wid, user, rest[2]))
            if (len(rest) == 4 and rest[0] == "smart-ui"
                    and rest[1] == "generations" and rest[3] == "view"):
                return self._send(200, generation_view(platform, wid, user, rest[2]))
            if (len(rest) == 4 and rest[0] == "smart-ui"
                    and rest[1] == "generations" and rest[3] == "replay"):
                return self._send(200, replay_generation(
                    platform, wid, user, rest[2]))
            if rest == ["smart-ui", "drafts"]:
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                return self._send(200, list_drafts(platform, wid, user))
            if rest == ["applications"]:
                return self._send(200, platform.applications.list(wid, actor=user))
            if rest == ["rules"]:
                return self._send(200, {"rules": platform.rules.list(
                    wid, user, lifecycle=query.get("lifecycle"))})
            if rest == ["event-contracts"]:
                return self._send(200, {"contracts":
                                        platform.business_events.list_contracts(
                                            wid, user,
                                            lifecycle=query.get("lifecycle"))})
            if rest == ["event-subscriptions"]:
                return self._send(200, {"subscriptions":
                                        platform.business_events
                                        .list_subscriptions(
                                            wid, user,
                                            event_type=query.get("event_type"))})
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
                platform.ctx.require(user, wid, "viewer")
                return self._send(200, platform.events.list(
                    wid, limit=self._query_int(query, "limit", 200),
                    event_type=query.get("type")))
            if rest == ["events", "dead-letters"]:
                platform.ctx.require(user, wid, "viewer")
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
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "ui-artifact" and rest[3] == "view":
                return self._send(200, application_ui_view(
                    platform, wid, user, rest[1]))
            if rest and rest[0] == "applications" and len(rest) == 3 \
                    and rest[2] == "spec":
                return self._send(200, platform.applications.get_spec(
                    wid, rest[1], user))
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
            if rest == ["state", "export"]:
                object_type = query.get("type")
                if not object_type:
                    raise ValidationError("query param 'type' is required")
                raw = gateway_of(platform, wid).find_objects(str(object_type))
                rows = raw.get("objects") if isinstance(raw, dict) else raw
                result = platform.exports.export_objects(
                    wid, user, list(rows or []),
                    fmt=str(query.get("fmt") or "json").lower(),
                    resource_type=str(object_type))
                return self._send_bytes(200, result["content_type"],
                                        result["body"],
                                        result.get("filename", ""))
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
                # Authorization is applied after the kernel query so the
                # response, pagination and total all reflect the caller's row
                # scope/field masks (not just the query filters).
                if isinstance(result, dict):
                    result = {**result,
                              "objects": platform.security.filter_objects(
                                  wid, user, result.get("objects") or [],
                                  resource_type=str(object_type))}
                else:
                    result = platform.security.filter_objects(
                        wid, user, result, resource_type=str(object_type))
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
                ref = query.get("ref", "")
                obj = gateway_of(platform, wid).get_object(ref)
                if obj is None:
                    return self._send(200, None)
                visible = platform.security.filter_objects(
                    wid, user, [obj],
                    resource_type=str(ref).partition("/")[0] or "object")
                return self._send(200, visible[0] if visible else None)
            if rest == ["state", "related"]:
                refs = gateway_of(platform, wid).related(
                    query.get("ref", ""), query.get("relation", ""),
                    direction=query.get("direction", "out"))
                visible = []
                for target in refs:
                    obj = gateway_of(platform, wid).get_object(target)
                    if obj is None:
                        continue
                    kept = platform.security.filter_objects(
                        wid, user, [obj],
                        resource_type=str(target).partition("/")[0]
                        or "object")
                    if kept:
                        visible.append(target)
                return self._send(200, visible)
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
                platform.ctx.require(user, wid, "viewer")
                changes = gateway_of(platform, wid).observe_changes(
                    self._query_int(query, "after", 0))
                limit = self._query_int(query, "limit", 0)
                return self._send(200, changes[-limit:] if limit else changes)
            if rest == ["audit"]:
                platform.ctx.require_action(user, wid, "security.read")
                return self._send(200, platform.ctx.audit.list(
                    wid, limit=self._query_int(query, "limit", 50)))
            raise ValidationError(f"no route for {self.path}")

        def _get_standards(self, wid: str, rest: list[str], query: dict,
                           user: str) -> None:
            """Standard-format plugins: list adapters, export the published TBox.

            Both routes are GETs because P0 export is read-only: producing a
            Turtle document never mutates the ontology, so there is no body
            to CSRF-check.  A missing ``adapter`` parameter, an unsupported
            ``format``, or an adapter the registry knows but cannot run is a
            400; a name that is not registered at all is a 404, and a
            workspace without a published ontology is a 404 as well.
            """
            if getattr(platform, "standards", None) is None:
                raise NotFoundError(
                    "standards adapters are not available in this deployment")
            if rest == ["adapters"]:
                return self._send(200, {
                    "adapters": platform.standards.list_adapters(wid, user)})
            if rest == ["export"]:
                adapter = str(query.get("adapter") or "").strip()
                if not adapter:
                    raise ValidationError(
                        "standard export requires ?adapter=<name>")
                artifact = platform.standards.export(
                    wid, user, adapter,
                    str(query.get("format") or "").strip() or None)
                metadata = artifact.get("metadata") or {}
                headers = {
                    "X-Adapter-Name": str(
                        metadata.get("adapter") or artifact.get("adapter") or ""),
                    "X-Adapter-Version": str(
                        metadata.get("version")
                        or metadata.get("adapter_version") or ""),
                    "X-Ontology-Version": str(
                        metadata.get("ontology_version", "")),
                    "X-Schema-Fingerprint": str(
                        metadata.get("schema_fingerprint", "")),
                }
                return self._send_bytes(
                    200, artifact["media_type"], artifact["content"],
                    artifact["filename"], headers=headers)
            raise ValidationError(f"no standards route for {self.path}")

        # ------------------------------------------------ workflow routes
        #
        # All workflow resources live below /ontology/workflow so the console
        # (and an agent) can discover them next to the ontology they govern.
        # Reads are viewer-level; every write goes through the use case, which
        # enforces editor and the round's editable/accepted lifecycle.

        def _workflow_get(self, wid: str, rest: list[str], query: dict,
                          user: str) -> None:
            if rest == []:
                return self._send(200, platform.workflow.overview(
                    wid, user, round_id=str(
                        query.get("round_id") or query.get("round") or "")))
            if rest == ["rounds"]:
                return self._send(200, {
                    "rounds": platform.workflow.overview(
                        wid, user).get("rounds", [])})
            if len(rest) >= 2 and rest[0] == "rounds":
                round_id = rest[1]
                tail = rest[2:]
                if not tail:
                    return self._send(200, platform.workflow.detail(
                        wid, user, round_id))
                if tail == ["trace-matrix"]:
                    return self._send(200, platform.workflow.trace_matrix(
                        wid, user, round_id))
                if tail == ["publish-check"]:
                    return self._send(200, platform.workflow.publish_check(
                        wid, user, round_id))
                if tail == ["report"]:
                    return self._send(200, platform.workflow.report(
                        wid, user, round_id))
                if len(tail) == 2 and tail[0] == "cqs" and tail[1] == "export":
                    return self._send(200, platform.workflow.export_cqs(
                        wid, user, round_id,
                        str(query.get("format") or "csv")))
                if len(tail) == 1 and tail[0] in (
                        "cqs", "reuse", "samples", "runs", "reviews"):
                    detail = platform.workflow.detail(wid, user, round_id)
                    return self._send(200, {tail[0]: detail.get(tail[0], [])})
            raise ValidationError(f"no workflow route for {self.path}")

        def _workflow_post(self, wid: str, rest: list[str],
                           body: dict) -> None:
            body = body or {}
            user = self._actor(body=body)
            if rest == ["rounds"]:
                return self._send(200, platform.workflow.create_round(
                    wid, user, body))
            if len(rest) >= 2 and rest[0] == "rounds":
                round_id = rest[1]
                tail = rest[2:]
                if tail == ["check"]:
                    return self._send(200, platform.workflow.check(
                        wid, user, round_id))
                if tail == ["bind"]:
                    return self._send(200, {
                        "round": platform.workflow.bind_round(
                            wid, user, round_id)})
                if tail == ["next"]:
                    return self._send(200, platform.workflow.next_round(
                        wid, user, round_id, body))
                if tail == ["publish"]:
                    return self._send(200, platform.workflow.publish(
                        wid, user, round_id))
                if tail == ["cqs"]:
                    return self._send(200, platform.workflow.add_cq(
                        wid, user, round_id, body))
                if tail == ["cqs", "import"]:
                    return self._send(200, platform.workflow.import_cqs(
                        wid, user, round_id, body))
                if len(tail) == 3 and tail[0] == "cqs" and tail[2] == "run":
                    return self._send(200, platform.workflow.preview_cq(
                        wid, user, round_id, tail[1], body))
                if tail == ["reuse"]:
                    return self._send(200, platform.workflow.add_reuse(
                        wid, user, round_id, body))
                if tail == ["samples"]:
                    return self._send(200, platform.workflow.add_sample(
                        wid, user, round_id, body))
                if tail == ["runs"]:
                    return self._send(200, platform.workflow.run_acceptance(
                        wid, user, round_id, body))
                if tail == ["reviews"]:
                    return self._send(200, platform.workflow.submit_review(
                        wid, user, round_id, body))
            raise ValidationError(f"no workflow route for {self.path}")

        def _workflow_patch(self, wid: str, rest: list[str],
                            body: dict) -> None:
            body = body or {}
            user = self._actor(body=body)
            if len(rest) >= 2 and rest[0] == "rounds":
                round_id = rest[1]
                tail = rest[2:]
                if not tail:
                    return self._send(200, platform.workflow.update_round(
                        wid, user, round_id, body))
                if len(tail) == 2 and tail[0] == "cqs":
                    return self._send(200, platform.workflow.update_cq(
                        wid, user, round_id, tail[1], body))
                if len(tail) == 2 and tail[0] == "reuse":
                    return self._send(200, platform.workflow.update_reuse(
                        wid, user, round_id, tail[1], body))
                if len(tail) == 2 and tail[0] == "samples":
                    return self._send(200, platform.workflow.update_sample(
                        wid, user, round_id, tail[1], body))
            raise ValidationError(f"no workflow route for {self.path}")

        def _workflow_delete(self, wid: str, rest: list[str],
                             query: dict) -> None:
            user = self._actor(query=query)
            if len(rest) == 4 and rest[0] == "rounds":
                round_id, kind, item_id = rest[1], rest[2], rest[3]
                if kind == "cqs":
                    return self._send(200, platform.workflow.delete_cq(
                        wid, user, round_id, item_id))
                if kind == "reuse":
                    return self._send(200, platform.workflow.delete_reuse(
                        wid, user, round_id, item_id))
                if kind == "samples":
                    return self._send(200, platform.workflow.delete_sample(
                        wid, user, round_id, item_id))
            raise ValidationError(f"no workflow route for {self.path}")

        # ------------------------------------------------------------ POST

        def do_POST(self) -> None:
            auth_context.clear()
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            try:
                self._check_origin()
                body = self._body()
                if parts == ["api", "llm"]:
                    return self._post_llm(body)
                if parts[:2] == ["scim", "v2"]:
                    platform.scim.authenticate(
                        self.headers.get("Authorization") or "")
                    if parts[2:3] == ["Users"]:
                        if len(parts) == 3:
                            return self._send(201, platform.scim.create_user(body))
                        if len(parts) == 4:
                            return self._send(200, platform.scim.patch_user(
                                parts[3], body))
                    if parts[2:3] == ["Groups"]:
                        if len(parts) == 3:
                            return self._send(201,
                                              platform.scim.create_group(body))
                        if len(parts) == 4:
                            return self._send(200, platform.scim.patch_group(
                                parts[3], body))
                    raise ValidationError(f"no SCIM route for {self.path}")
                if parts == ["api", "tenants"]:
                    return self._send(201, platform.tenants.create(
                        self._actor(body=body), body.get("name", "")))
                if len(parts) == 4 and parts[:2] == ["api", "tenants"] \
                        and parts[3] == "members":
                    return self._send(201, platform.tenants.add_member(
                        parts[2], self._actor(body=body),
                        body.get("member") or body.get("user", ""),
                        body.get("role", "viewer")))
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

        def _post_security(self, wid: str, rest: list[str], body: dict,
                           user: str) -> None:
            if rest == ["breakglass", "request"]:
                return self._send(201, platform.break_glass.request(
                    wid, user, str(body.get("reason") or ""),
                    ticket=str(body.get("ticket") or ""),
                    minutes=int(body.get("minutes") or 30),
                    role=str(body.get("role") or "owner"),
                    mfa_code=str(body.get("mfa_code") or "")))
            if len(rest) == 3 and rest[:1] == ["breakglass"] \
                    and rest[2] in ("approve", "revoke"):
                if rest[2] == "approve":
                    return self._send(200, platform.break_glass.approve(
                        wid, rest[1], user,
                        mfa_code=str(body.get("mfa_code") or "")))
                return self._send(200, platform.break_glass.revoke(
                    wid, rest[1], user))
            if rest == ["mfa", "enroll"]:
                target = str(body.get("user") or user)
                if target != user:
                    platform.ctx.require_action(user, wid, "security.manage")
                return self._send(201, platform.mfa.enroll(target))
            if rest == ["mfa", "verify"]:
                code = str(body.get("code") or "")
                return self._send(200, {
                    "verified": platform.mfa.verify(user, code)})
            if rest == ["mfa", "disable"]:
                target = str(body.get("user") or user)
                if target != user:
                    platform.ctx.require_action(user, wid, "security.manage")
                return self._send(200, platform.mfa.disable(target))
            if rest == ["query"]:
                return self._send(200, {
                    "objects": platform.security.query_objects(
                        wid, user, str(body.get("kind") or ""),
                        filter=body.get("filter"),
                        limit=body.get("limit", 200))})
            if rest == ["cache", "clear"]:
                platform.ctx.require_action(user, wid, "security.manage")
                platform.ctx.security.invalidate_cache()
                return self._send(200, {"cleared": True})
            if rest == ["authorize"]:
                return self._send(200, platform.security.authorize(
                    wid, user, str(body.get("action") or ""),
                    resource_type=str(body.get("resource_type")
                                      or body.get("type") or "workspace"),
                    resource_id=str(body.get("resource_id")
                                    or body.get("id") or ""),
                    attributes=body.get("attributes") or {},
                    subject=str(body.get("subject") or "") or None))
            if rest == ["authorize", "preview"]:
                return self._send(200, platform.security.authorize(
                    wid, user, str(body.get("action") or ""),
                    resource_type=str((body.get("resource") or {}).get("type")
                                      or body.get("resource_type")
                                      or body.get("type") or "workspace"),
                    resource_id=str((body.get("resource") or {}).get("id")
                                    or body.get("resource_id")
                                    or body.get("id") or ""),
                    attributes=(body.get("resource") or {}).get("attributes")
                    or body.get("attributes") or {},
                    subject=str(body.get("subject") or "") or None,
                    record=False))
            if rest == ["blueprint", "validate"]:
                return self._send(200, platform.security.validate_blueprint(
                    wid, user, body.get("blueprint") or {},
                    known_objects=body.get("known_objects"),
                    known_actions=body.get("known_actions"),
                    known_fields=body.get("known_fields")))
            if rest == ["policies", "draft"]:
                return self._send(200, platform.policy_drafts.draft(
                    wid, user, str(body.get("prompt") or ""),
                    mode=str(body.get("mode") or "auto")))
            if rest == ["policies"]:
                return self._send(200, platform.security.create_policy(
                    wid, user, document=body.get("document"),
                    note=str(body.get("note") or "")))
            if len(rest) == 2 and rest[0] == "policies":
                # PUT-shaped update over POST keeps the stdlib handler small;
                # the lifecycle routes below remain explicit.
                return self._send(200, platform.security.update_policy(
                    wid, user, rest[1], body.get("document") or {},
                    note=str(body.get("note") or ""),
                    expected_version=self._required_version(body)))
            if len(rest) == 3 and rest[0] == "policies" \
                    and rest[2] == "validate":
                return self._send(200, platform.security.validate_policy(
                    wid, user, rest[1]))
            if len(rest) == 3 and rest[0] == "policies" \
                    and rest[2] == "dry-run":
                return self._send(200, platform.security.dry_run(
                    wid, user, rest[1], action=str(body.get("action") or ""),
                    resource_type=str(body.get("resource_type")
                                      or body.get("type") or "workspace"),
                    resource_id=str(body.get("resource_id")
                                    or body.get("id") or ""),
                    attributes=body.get("attributes") or {},
                    subject=(str(body.get("subject") or body.get("user") or "")
                             or None)))
            if len(rest) == 3 and rest[0] == "policies" \
                    and rest[2] == "approve":
                return self._send(200, platform.security.approve_policy(
                    wid, user, rest[1], comment=str(body.get("comment") or "")))
            if len(rest) == 3 and rest[0] == "policies" \
                    and rest[2] == "publish":
                return self._send(200, platform.security.publish_policy(
                    wid, user, rest[1],
                    expected_version=self._required_version(body)))
            if len(rest) == 3 and rest[0] == "policies" \
                    and rest[2] == "rollback":
                return self._send(200, platform.security.rollback_policy(
                    wid, user, rest[1],
                    expected_version=self._required_version(body)))
            if rest == ["bindings"]:
                return self._send(200, platform.security.create_binding(
                    wid, user, body))
            if rest == ["groups"]:
                return self._send(200, platform.security.create_group(
                    wid, user, body))
            if len(rest) == 2 and rest[0] == "groups":
                return self._send(200, platform.security.update_group(
                    wid, user, rest[1], body))
            if rest == ["relations"]:
                return self._send(200, platform.security.create_relation(
                    wid, user, body))
            if rest == ["relations", "delete"]:
                return self._send(200, platform.security.delete_relation(
                    wid, user, body))
            if rest == ["org", "units"]:
                return self._send(200, platform.organization.create_unit(
                    wid, user, str(body.get("name") or ""),
                    parent_id=str(body.get("parent_id") or "")))
            if len(rest) == 3 and rest[:2] == ["org", "units"] \
                    and rest[2] == "members":
                return self._send(200, platform.organization.set_members(
                    wid, user, str(body.get("unit_id") or ""),
                    body.get("members") or []))
            if len(rest) >= 3 and rest[:2] == ["org", "units"] \
                    and rest[-1] == "members":
                return self._send(200, platform.organization.set_members(
                    wid, user, rest[2], body.get("members") or []))
            if len(rest) == 3 and rest[:2] == ["org", "units"] \
                    and rest[2] == "delete":
                return self._send(200, platform.organization.delete_unit(
                    wid, user, str(body.get("unit_id") or ""),
                    cascade=body.get("cascade") is True))
            if len(rest) == 4 and rest[:2] == ["org", "units"] \
                    and rest[3] == "delete":
                return self._send(200, platform.organization.delete_unit(
                    wid, user, rest[2], cascade=body.get("cascade") is True))
            if rest == ["identity", "users"]:
                platform.ctx.require_action(user, wid, "security.manage")
                return self._send(200, platform.identity_directory.upsert_user(
                    str(body.get("user_name") or ""),
                    display_name=body.get("display_name"),
                    emails=body.get("emails"), groups=body.get("groups"),
                    active=body.get("active"),
                    external_id=body.get("external_id"),
                    provider=body.get("provider"), actor=user))
            if rest == ["identity", "login", "oidc"]:
                return self._send(200, platform.identity.oidc_login(
                    str(body.get("id_token") or "")))
            if rest == ["identity", "login", "ldap"]:
                return self._send(200, platform.identity.ldap_login(
                    str(body.get("username") or ""),
                    str(body.get("password") or "")))
            if rest == ["webhooks"]:
                hook = platform.webhooks.create_webhook(
                    wid, user, str(body.get("url") or ""),
                    body.get("event_types") or [],
                    secret=str(body.get("secret") or ""))
                return self._send(200, {**hook, "secret": "***"})
            if len(rest) == 2 and rest[0] == "webhooks":
                hook = platform.webhooks.update_webhook(
                    wid, user, rest[1], url=body.get("url"),
                    event_types=body.get("event_types"),
                    secret=body.get("secret"), active=body.get("active"))
                return self._send(200, {**hook, "secret": "***"})
            if len(rest) == 3 and rest[0] == "webhooks" \
                    and rest[2] == "delete":
                return self._send(200, platform.webhooks.delete_webhook(
                    wid, user, rest[1]))
            if rest == ["async", "tasks"]:
                return self._send(200, platform.async_tasks.enqueue(
                    wid, user, str(body.get("function") or ""),
                    target=body.get("target"),
                    args=body.get("args") or {},
                    delay_seconds=float(body.get("delay_seconds") or 0)))
            if len(rest) == 3 and rest[0] == "async" \
                    and rest[1] == "tasks" and rest[2] == "run":
                platform.ctx.require_action(user, wid, "security.manage")
                return self._send(200, platform.async_tasks.run_now(
                    str(body.get("task_id") or "")))
            raise ValidationError("no security route")

        def _post(self, wid: str | None, rest: list[str], body: dict) -> None:
            user = self._actor(body=body)
            if wid is None:
                if rest == []:
                    return self._send(200, platform.workspaces.create(
                        body.get("name", ""), actor=user,
                        description=body.get("description", ""),
                        tenant_id=body.get("tenant_id")))
                raise ValidationError("no route")
            if rest == ["delete"]:
                return self._send(200, platform.workspaces.delete(
                    wid, actor=user, confirm=body.get("confirm") is True))
            if rest == ["reset"]:
                return self._send(200, platform.workspaces.reset(
                    wid, actor=user, confirm=body.get("confirm") is True,
                    confirm_name=str(body.get("confirm_name") or "")))
            if rest == ["members"]:
                return self._send(200, platform.workspaces.add_member(
                    wid, body.get("member") or body["user_id"], body["role"],
                    actor=user))
            if rest[:1] == ["security"]:
                return self._post_security(wid, rest[1:], body, user)
            if rest == ["llm-config"]:
                return self._send(200, platform.llm_config.put(wid, user, body))
            if rest == ["llm-config", "check"]:
                return self._send(200, platform.llm_config.check(wid, user))
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.create(
                    wid, body.get("name", ""), actor=user,
                    description=body.get("description", ""),
                    profile=body.get("profile", "closed")))
            if rest[:2] == ["ontology", "workflow"]:
                return self._workflow_post(wid, rest[2:], body)
            if rest == ["ontology", "discard"]:
                return self._send(200, platform.ontologies.discard_draft(
                    wid, user))
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
            if len(rest) == 3 and rest[0] == "datasources" and rest[2] == "test":
                return self._send(200, platform.data.test_source(
                    wid, user, rest[1]))
            if len(rest) == 3 and rest[0] == "datasources" and rest[2] == "preview":
                return self._send(200, platform.data.preview_source(
                    wid, user, rest[1], body.get("asset"), body.get("limit")))
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
            if len(rest) == 3 and rest[0] == "capabilities" and rest[2] == "clone":
                return self._send(200, platform.capabilities.clone(
                    wid, user, rest[1], key=body.get("key"),
                    name=body.get("name")))
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
            if rest == ["smart-ui", "config"]:
                return self._send(200, set_workspace_pack(
                    platform, wid, user, str(body.get("pack_id") or "")))
            if rest == ["smart-ui", "rule-packs"]:
                return self._send(201, create_rule_pack(
                    platform, wid, user, body))
            if rest == ["smart-ui", "rule-packs", "validate"]:
                return self._send(200, validate_rule_pack(
                    platform, wid, user,
                    body.get("pack") if isinstance(body.get("pack"), dict)
                    else body))
            if rest == ["smart-ui", "rule-packs", "dry-run"]:
                return self._send(200, dry_run_rule_pack(
                    platform, wid, user, str(body.get("rule_pack_id") or ""),
                    body))
            if rest == ["smart-ui", "rule-packs", "bind"]:
                return self._send(200, bind_rule_pack(
                    platform, wid, user, str(body.get("rule_pack_id") or "")))
            if (len(rest) == 4 and rest[:2] == ["smart-ui", "rule-packs"]
                    and rest[3] in {"publish", "rollback", "archive",
                                    "clone", "dry-run"}):
                pack_id = rest[2]
                action = rest[3]
                if action == "publish":
                    return self._send(200, publish_rule_pack(
                        platform, wid, user, pack_id,
                        note=str(body.get("note") or "")))
                if action == "rollback":
                    return self._send(200, rollback_rule_pack(
                        platform, wid, user, pack_id))
                if action == "archive":
                    return self._send(200, archive_rule_pack(
                        platform, wid, user, pack_id))
                if action == "clone":
                    return self._send(201, clone_rule_pack(
                        platform, wid, user, pack_id,
                        name=str(body.get("name") or "")))
                return self._send(200, dry_run_rule_pack(
                    platform, wid, user, pack_id, body))
            if rest == ["smart-ui", "generations"]:
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                generation = start_generation(
                    platform, wid, user,
                    question=str(body.get("question") or ""),
                    roles=[str(role) for role in (body.get("roles") or [])],
                    mode=str(body.get("mode") or "auto"),
                    focus=body.get("focus") if isinstance(body.get("focus"), dict)
                    else None,
                    pack_id=str(body.get("pack_id") or ""),
                    rule_pack_id=str(body.get("rule_pack_id")
                                     or body.get("rulePackId") or ""))
                return self._send(202, generation)
            if (len(rest) == 4 and rest[0] == "smart-ui"
                    and rest[1] == "generations" and rest[3] == "cancel"):
                return self._send(200, cancel_generation(
                    platform, wid, user, rest[2]))
            if (len(rest) == 4 and rest[0] == "smart-ui"
                    and rest[1] == "generations" and rest[3] == "refine"):
                if not studio_enabled():
                    raise NotFoundError("smart-UI Studio is disabled")
                return self._send(202, refine_generation(
                    platform, wid, user, rest[2],
                    str(body.get("instruction") or ""),
                    focus=body.get("focus") if isinstance(body.get("focus"), dict)
                    else None))
            if (len(rest) == 4 and rest[0] == "smart-ui"
                    and rest[1] == "generations" and rest[3] == "accept"):
                return self._send(200, accept_generation(
                    platform, wid, user, rest[2],
                    name=str(body.get("name") or "")))
            if (len(rest) == 4 and rest[0] == "smart-ui"
                    and rest[1] == "generations" and rest[3] == "publish"):
                return self._send(200, publish_generation(
                    platform, wid, user, rest[2],
                    name=str(body.get("name") or ""),
                    application_id=str(body.get("application_id") or "")))
            if rest == ["smart-ui", "compile"]:
                return self._send(200, compile_smart_ui(
                    platform, wid, user,
                    question=str(body.get("question") or ""),
                    roles=[str(role) for role in (body.get("roles") or [])],
                    focus=body.get("focus") if isinstance(body.get("focus"), dict)
                    else None))
            if len(rest) == 3 and rest[0] == "smart-ui" and rest[1] == "actions":
                return self._send(200, invoke_smart_action(
                    platform, wid, user, rest[2], body))
            # ---- P1 rules / business events (contracts, subscriptions, emit)
            if rest == ["rules"]:
                return self._send(200, platform.rules.create(
                    wid, user, body["name"], body["object_type"],
                    body["condition"],
                    description=body.get("description", ""),
                    severity=body.get("severity", "error"),
                    when=body.get("when"),
                    message=body.get("message", ""),
                    priority=body.get("priority", 100)))
            if len(rest) == 3 and rest[0] == "rules" and rest[2] == "publish":
                return self._send(200, platform.rules.publish(
                    wid, user, rest[1]))
            if rest == ["rules", "evaluate"]:
                return self._send(200, platform.rules.evaluate(
                    wid, user, object_type=body.get("object_type"),
                    refs=body.get("refs"),
                    include_drafts=bool(body.get("include_drafts", False))))
            if rest == ["event-contracts"]:
                try:
                    version = int(body.get("version") or 1)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        "event contract version must be an integer") from exc
                return self._send(
                    200, platform.business_events.create_contract(
                        wid, user, body["name"], body["aggregate_kind"],
                        body["payload_schema"],
                        description=body.get("description", ""),
                        version=version))
            if len(rest) == 3 and rest[0] == "event-contracts" \
                    and rest[2] == "publish":
                return self._send(
                    200, platform.business_events.publish_contract(
                        wid, user, rest[1]))
            if rest == ["event-subscriptions"]:
                return self._send(200, platform.business_events.subscribe(
                    wid, user, body["name"], body["event_type"],
                    filter=body.get("filter"),
                    handler_key=body.get("handler_key", ""),
                    enabled=body.get("enabled", True)))
            if rest == ["events", "emit"]:
                return self._send(200, platform.business_events.emit(
                    wid, user, body["event_type"], body["aggregate_kind"],
                    body["aggregate_id"], payload=body.get("payload")))
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
            if len(rest) == 3 and rest[0] == "applications" \
                    and rest[2] == "interactions":
                interaction_id = (body.get("interaction_id")
                                  or body.get("id") or "")
                return self._send(200, platform.applications.add_interaction(
                    wid, rest[1], user, str(interaction_id), body["kind"],
                    title=body.get("title", ""),
                    capability_id=body.get("capability_id", ""),
                    config=body.get("config"),
                    permissions=body.get("permissions"),
                    approval=body.get("approval")))
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
                    query_alias=body.get("query_alias", ""),
                    config=body.get("config")))
            if len(rest) == 3 and rest[0] == "applications" and rest[2] == "clone":
                return self._send(200, platform.applications.clone(
                    wid, rest[1], user, name=body.get("name")))
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

        # ------------------------------------------------------------ PUT

        def do_PUT(self) -> None:
            auth_context.clear()
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            query = {k: _parse_scalar(v[-1]) for k, v in parse_qs(parsed.query).items()}
            try:
                self._check_origin()
                body = self._body()
                if parts[:2] == ["scim", "v2"]:
                    platform.scim.authenticate(
                        self.headers.get("Authorization") or "")
                    if parts[2:3] == ["Groups"] and len(parts) == 4:
                        return self._send(200, platform.scim.replace_group(
                            parts[3], body))
                    raise ValidationError(f"no SCIM route for {self.path}")
                if parts[:2] != ["api", "workspaces"] or len(parts) < 3:
                    raise ValidationError(f"no route for {self.path}")
                wid = parts[2]
                rest = parts[3:]
                if rest == ["llm-config"]:
                    user = self._actor(body=body, query=query)
                    return self._send(200, platform.llm_config.put(wid, user, body))
                if len(rest) == 3 and rest[:2] == ["smart-ui", "drafts"]:
                    if not studio_enabled():
                        raise NotFoundError("smart-UI Studio is disabled")
                    user = self._actor(body=body, query=query)
                    return self._send(200, update_draft(
                        platform, wid, user, rest[2],
                        str(body.get("name") or "")))
                if len(rest) == 3 and rest[:2] == ["smart-ui", "rule-packs"]:
                    user = self._actor(body=body, query=query)
                    return self._send(200, update_rule_pack(
                        platform, wid, user, rest[2], body))
                if len(rest) == 3 and rest[0] == "security" \
                        and rest[1] == "policies":
                    user = self._actor(body=body, query=query)
                    return self._send(200, platform.security.update_policy(
                        wid, user, rest[2], body.get("document") or {},
                        note=str(body.get("note") or ""),
                        expected_version=self._required_version(body)))
                raise ValidationError(
                    f"PUT is only supported for /api/workspaces/{{wid}}/llm-config; "
                    f"no route for {self.path}")
            except PlatformError as exc:
                return self._error(exc)
            except (KeyError, json.JSONDecodeError) as exc:
                return self._error(ValidationError(f"invalid request body: {exc}"))
            except Exception as exc:  # pragma: no cover
                return self._error(exc)

        # ------------------------------------------------------------ PATCH

        def do_PATCH(self) -> None:
            auth_context.clear()
            parsed = urlparse(self.path)
            parts = [unquote(p) for p in parsed.path.split("/") if p]
            query = {k: _parse_scalar(v[-1]) for k, v in parse_qs(parsed.query).items()}
            try:
                self._check_origin()
                body = self._body()
                if parts[:2] == ["scim", "v2"]:
                    platform.scim.authenticate(
                        self.headers.get("Authorization") or "")
                    if parts[2:3] == ["Users"] and len(parts) == 4:
                        return self._send(200, platform.scim.patch_user(
                            parts[3], body))
                    if parts[2:3] == ["Groups"] and len(parts) == 4:
                        return self._send(200, platform.scim.patch_group(
                            parts[3], body))
                    raise ValidationError(f"no SCIM route for {self.path}")
                if parts[:2] != ["api", "workspaces"] or len(parts) < 3:
                    raise ValidationError(f"no route for {self.path}")
                wid = parts[2]
                rest = parts[3:]
                return self._patch(wid, rest, query, body)
            except (KeyError, json.JSONDecodeError) as exc:
                return self._error(ValidationError(f"invalid request body: {exc}"))
            except Exception as exc:  # pragma: no cover
                return self._error(exc)

        def _patch(self, wid: str, rest: list[str], query: dict,
                   body: dict) -> None:
            # PATCH accepts the actor from the body or the query string, like
            # POST and DELETE respectively (token mode still overrides both)
            declared = body.pop("user", None) or (query or {}).get("user")
            user = self._actor(
                body={"user": declared} if declared else None, query=query)
            if rest[:2] == ["ontology", "workflow"]:
                return self._workflow_patch(wid, rest[2:], body)
            if rest == ["ontology"]:
                return self._send(200, platform.ontologies.update_meta(
                    wid, user, name=body.get("name"),
                    description=body.get("description"),
                    profile=body.get("profile"),
                    expected_version=self._expected_version(body)))
            if len(rest) == 2 and rest[0] == "capabilities":
                return self._send(200, platform.capabilities.update(
                    wid, user, rest[1], key=body.get("key"),
                    name=body.get("name"),
                    target_type=body.get("target_type"),
                    description=body.get("description"),
                    inputs=body.get("inputs"),
                    implementation=body.get("implementation"),
                    contract=body.get("contract")))
            if len(rest) == 2 and rest[0] == "datasources":
                return self._send(200, platform.data.update_source(
                    wid, user, rest[1], name=body.get("name"),
                    config=body.get("config"), assets=body.get("assets"),
                    kind=body.get("kind"),
                    test_connection=bool(body.get("test_connection", True))))
            if len(rest) == 2 and rest[0] == "projections":
                return self._send(200, platform.data.update_projection(
                    wid, user, rest[1], name=body.get("name"),
                    object_mappings=body.get("object_mappings"),
                    relation_mappings=body.get("relation_mappings"),
                    mode=body.get("mode"), row_limit=body.get("row_limit"),
                    datasource_id=body.get("datasource_id")))
            if len(rest) == 2 and rest[0] == "applications":
                return self._send(200, platform.applications.update(
                    wid, rest[1], user, name=body.get("name"),
                    description=body.get("description")))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "views":
                return self._send(200, platform.applications.update_view(
                    wid, rest[1], user, rest[3], title=body.get("title"),
                    kind=body.get("kind"),
                    object_type=body.get("object_type"),
                    properties=body.get("properties"),
                    query_alias=body.get("query_alias"),
                    config=body.get("config")))
            if len(rest) == 4 and rest[0] == "applications" \
                    and rest[2] == "queries":
                return self._send(200, platform.applications.update_query(
                    wid, rest[1], user, rest[3], title=body.get("title"),
                    object_type=body.get("object_type"),
                    filters=body.get("filters"), expand=body.get("expand")))
            if rest == ["state", "objects"]:
                ref = query.get("ref")
                if not ref:
                    raise ValidationError("query param 'ref' is required")
                properties = body.get("properties")
                if properties is None:
                    raise ValidationError("body field 'properties' is required")
                return self._send(200, platform.state.update_object(
                    wid, user, str(ref), properties))
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
