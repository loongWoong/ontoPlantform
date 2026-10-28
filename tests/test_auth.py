"""0.8 控制面安全：token 身份、provider 注入禁止、LLM 一次性配置。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from onto_platform.interfaces.api import build_handler
from tests.conftest import make_platform, seed_workspace


def _serve(platform):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def get(base, path, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def post(base, path, body, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def token_server(monkeypatch):
    monkeypatch.setenv("ONTO_API_TOKEN", "s3cret-token")
    monkeypatch.setenv("ONTO_API_TOKEN_ACTOR", "token-user")
    platform = make_platform()
    seed_workspace(platform)
    httpd, base = _serve(platform)
    yield base
    httpd.shutdown()


@pytest.fixture()
def open_server():
    platform = make_platform()
    seed_workspace(platform)
    httpd, base = _serve(platform)
    yield base
    httpd.shutdown()


def test_missing_or_wrong_token_is_denied(token_server):
    status, body = get(token_server, "/api/workspaces")
    assert status == 403 and "token" in body["error"]
    status, _ = get(token_server, "/api/workspaces", token="wrong")
    assert status == 403
    status, workspaces = get(token_server, "/api/workspaces", token="s3cret-token")
    assert status == 200 and isinstance(workspaces, list)


def test_actor_comes_from_token_not_body(token_server):
    # the body tries to impersonate "admin"; the token identity must win
    status, ws = post(token_server, "/api/workspaces",
                      {"name": "auth", "user": "admin"}, token="s3cret-token")
    assert status == 200
    status, events = get(token_server, f"/api/workspaces/{ws['id']}/audit?limit=10",
                         token="s3cret-token")
    assert status == 200
    created = [e for e in events if e["action"] == "workspace.create"]
    assert created and created[0]["actor"] == "token-user"


def test_open_mode_keeps_declared_user(open_server):
    status, ws = post(open_server, "/api/workspaces", {"name": "dev", "user": "alice"})
    assert status == 200
    status, events = get(open_server, f"/api/workspaces/{ws['id']}/audit?limit=10")
    created = [e for e in events if e["action"] == "workspace.create"]
    assert created and created[0]["actor"] == "alice"


def test_provider_not_injectable_per_request(open_server):
    status, ws = post(open_server, "/api/workspaces", {"name": "draft"})
    assert status == 200
    status, body = post(
        open_server, f"/api/workspaces/{ws['id']}/ontology/draft",
        {"text": "收费站", "llm": {"provider": "openai", "api_key": "sk-leak"}})
    assert status == 400
    assert "injection is disabled" in body["error"]


def test_llm_is_configured_once_server_side(open_server):
    status, health = get(open_server, "/api/health")
    assert status == 200 and health["llm_configured"] is False
    status, configured = post(open_server, "/api/llm", {
        "provider": "openai", "base_url": "http://127.0.0.1:1/v1",
        "model": "test-model", "api_key": "sk-test",
    })
    assert status == 200 and configured["ok"] is True
    status, health = get(open_server, "/api/health")
    assert health["llm_configured"] is True
    assert health["llm_provider"] == "OpenAICompatProvider"
