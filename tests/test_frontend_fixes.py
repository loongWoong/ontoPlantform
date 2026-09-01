"""Regressions for the frontend-completeness fixes (docs/frontend-audit.md).

B1  change log survives process restart (sqlite runtime)
B2  application query expand accepts plain strings
B3  state/find contains/in keep raw strings for numeric-looking needles
B4  /api/health exposes llm_configured
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from onto_platform.bootstrap import PlatformConfig, build_platform
from onto_platform.domain.application.model import QuerySpec
from onto_platform.infrastructure.runtime.kernel_gateway import KernelRuntimeGateway
from onto_platform.ports.runtime import SemanticDelta, ObjectUpsert
from tests.conftest import make_platform, seed_workspace
from tests.test_api import post


# ---------------------------------------------------------------- B2 expand

def test_query_spec_coerce_expand_strings():
    spec = QuerySpec(alias="q", object_type="Vehicle",
                     expand=QuerySpec.coerce_expand(["passes", "  ", {"relation": "owns"}]))
    assert spec.to_dict()["expand"] == [
        {"relation": "passes", "direction": "out"},
        {"relation": "owns", "direction": "out"},
    ]


def test_query_spec_from_dict_with_string_expand():
    spec = QuerySpec.from_dict({"alias": "q", "expand": ["passes"]})
    assert spec.expand == [{"relation": "passes", "direction": "out"}]


def test_add_query_with_string_expand_over_http():
    platform = make_platform()
    wid = seed_workspace(platform)
    app = platform.applications.create(wid, "admin", "测试应用")
    # used to raise ValueError (dict("passes")) -> HTTP 500
    updated = platform.applications.add_query(
        wid, app["id"], "admin", "q1", "Vehicle", expand=["passes"])
    assert updated["queries"]["q1"]["expand"] == [
        {"relation": "passes", "direction": "out"}]
    result = platform.applications.run_query(wid, app["id"], "admin", "q1")
    expansion = result["expand"][0]
    assert expansion["relation"] == "passes" and expansion["direction"] == "out"
    # seeded workspace: V001 passes ST001+ST002, V002 passes ST001
    assert len(expansion["objects"]) == 3


# ---------------------------------------------------------------- B3 filters

def test_contains_filter_with_numeric_looking_needle():
    platform = make_platform()
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    # "12345" parses as JSON int; contains against a string property must
    # still match (the API layer keeps the raw string for text operators)
    hits = gateway.find_objects("Vehicle", {"plate__contains": "12345"})
    assert [o["id"] for o in hits] == ["V001"]


def test_numeric_eq_filter_still_parses():
    platform = make_platform()
    wid = seed_workspace(platform)
    gateway = platform.ctx.gateway_for(wid)
    assert [o["id"] for o in gateway.find_objects("Vehicle", {"balance__gt": 50})] == ["V001"]


def test_in_filter_receives_parsed_list_over_http():
    """The console serializes an `in` list as "V001,V002" in the query string;
    the API layer must split it back into a real list, otherwise the kernel's
    `actual not in expected` degrades to substring matching ("V00" would hit)."""
    import threading
    import urllib.request
    from urllib.parse import quote

    from onto_platform.interfaces.api import build_handler

    platform = make_platform()
    wid = seed_workspace(platform)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def find(query: str):
        with urllib.request.urlopen(
                f"{base}/api/workspaces/{wid}/state/find?{query}&user=admin") as res:
            return json.loads(res.read().decode("utf-8"))

    try:
        hits = find(f"type=Vehicle&plate__in={quote('鲁A12345,鲁B67890')}")
        assert sorted(o["id"] for o in hits) == ["V001", "V002"]

        # substring trap: "鲁" is contained in both plates but matches nothing
        assert find(f"type=Vehicle&plate__in={quote('鲁')}") == []

        # numeric members survive the round-trip
        hits = find("type=Vehicle&balance__in=20,100")
        assert sorted(o["id"] for o in hits) == ["V001", "V002"]
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- B1 changes

def test_change_log_survives_gateway_recreation(tmp_path):
    log = tmp_path / "changes.jsonl"
    gw1 = KernelRuntimeGateway(store_kind="memory", change_log_path=str(log))
    gw1.publish_schema(_minimal_payload())
    gw1.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Thing", id="T1", properties={"name": "x"}),)))
    gw1.apply_delta(SemanticDelta(
        upserts=(ObjectUpsert(type="Thing", id="T1", properties={"name": "y"}),),
    ), source="invoke")
    assert len(gw1.observe_changes()) == 2

    # a "restarted" process rebuilds the gateway from disk
    gw2 = KernelRuntimeGateway(store_kind="memory", change_log_path=str(log))
    events = gw2.observe_changes()
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["source"] == "invoke"


def _minimal_payload():
    from onto_platform.ports.runtime import SchemaPayload
    return SchemaPayload(
        schema={
            "version": 1,
            "objects": {"Thing": {"properties": {"name": {"type": "string"}}}},
            "relations": {},
            "functions": {},
        },
        bindings={}, workflows={},
    )


# ---------------------------------------------------------------- B4 health

def test_health_reports_llm_configured():
    platform = make_platform()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                __import__("onto_platform.interfaces.api",
                                           fromlist=["build_handler"]).build_handler(platform))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/health") as res:
            health = json.loads(res.read().decode("utf-8"))
        assert health["llm_configured"] is False
    finally:
        httpd.shutdown()
