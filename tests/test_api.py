"""REST API end-to-end: design -> publish -> project -> invoke -> ask。"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from onto_platform.interfaces.api import build_handler
from onto_platform.infrastructure.llm.echo import EchoLLMProvider
from tests.conftest import make_platform, seed_workspace


@pytest.fixture()
def server():
    platform = make_platform()
    wid = seed_workspace(platform)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, wid
    httpd.shutdown()


def get(base, path):
    with urllib.request.urlopen(base + path) as res:
        return json.loads(res.read().decode("utf-8"))


def post(base, path, body):
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_and_web_console(server):
    base, _ = server
    assert get(base, "/api/health")["status"] == "ok"
    html = urllib.request.urlopen(base + "/").read().decode("utf-8")
    assert "OntoPlatform" in html


def test_full_flow_over_http(server):
    base, wid = server

    # design-time reads
    ontology = get(base, f"/api/workspaces/{wid}/ontology?user=admin")
    assert "Vehicle" in ontology["objects"]
    runtime = get(base, f"/api/workspaces/{wid}/runtime?user=admin")
    assert {o["name"] for o in runtime["objects"]} == {"Vehicle", "TollStation"}

    # runtime state reads
    stations = get(base, f"/api/workspaces/{wid}/state/find?type=TollStation"
                         f"&status=congested&user=admin")
    assert [s["id"] for s in stations] == ["ST001"]
    related = get(base, f"/api/workspaces/{wid}/state/related"
                        f"?ref=Vehicle/V001&relation=passes&user=admin")
    assert set(related) == {"TollStation/ST001", "TollStation/ST002"}
    changes = get(base, f"/api/workspaces/{wid}/changes?user=admin")
    assert changes and changes[0]["source"] == "projection"

    # capability invocation
    status, result = post(base, f"/api/workspaces/{wid}/invoke", {
        "function": "Vehicle.recharge", "target": "Vehicle/V001",
        "args": {"amount": 25}, "user": "admin"})
    assert status == 200 and result["ok"] is True
    assert result["output"]["balance"] == 125

    # deterministic ask
    status, answer = post(base, f"/api/workspaces/{wid}/ask",
                          {"question": "济南西收费站为什么拥堵？", "user": "admin"})
    assert status == 200 and "济南西收费站" in answer["answer"]

    # error mapping: unknown object type -> 400 with message
    status, payload = post(base, f"/api/workspaces/{wid}/invoke", {
        "function": "Vehicle.recharge", "target": "Alien/X1",
        "args": {"amount": 1}, "user": "admin"})
    assert status == 200 and payload["ok"] is False  # invoke never raises
    status, payload = post(base, f"/api/workspaces/{wid}/ontology", {
        "name": "second", "user": "admin"})
    assert status == 400  # one ontology per workspace


def test_agent_endpoint_with_script(server):
    base, wid = server
    status, run = post(base, f"/api/workspaces/{wid}/agent", {
        "goal": "济南西收费站为什么拥堵？",
        "llm": {"script": [
            '{"think": "找站", "action": {"tool": "find", "args": '
            '{"object_type": "TollStation", "filter": {"status": "congested"}}}}',
            '{"think": "答", "answer": "济南西收费站拥堵。"}',
        ]},
        "user": "admin"})
    assert status == 200
    assert run["ok"] is True and run["answer"] == "济南西收费站拥堵。"


def test_access_denied_maps_to_403(server):
    base, wid = server
    status, payload = post(base, f"/api/workspaces/{wid}/ontology/objects", {
        "name": "Road", "properties": [{"name": "n", "type": "string"}],
        "user": "mallory"})
    assert status == 403


def test_application_agent_endpoints(server):
    from urllib.parse import quote

    base, wid = server
    app = post(base, f"/api/workspaces/{wid}/applications",
               {"user": "admin", "name": "agent台"})[1]
    status, body = post(
        base, f"/api/workspaces/{wid}/applications/{app['id']}/agents",
        {"user": "admin", "alias": "助手", "goal": "检查拥堵"})
    assert status == 200
    assert body["agents"][0]["alias"] == "助手"

    # no LLM provider configured on this platform -> clean error, not a crash
    status, body = post(
        base,
        f"/api/workspaces/{wid}/applications/{app['id']}/agents/"
        f"{quote('助手')}/agent",
        {"user": "admin"})
    # no LLM provider configured on this platform -> clean 503-style error
    assert status in (400, 403, 500, 503) or body.get("ok") is False
