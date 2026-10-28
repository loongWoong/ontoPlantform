"""AI-assisted ontology drafting: one paragraph -> objects / relations / terms.

The LLM is a *proposal* engine: every element it returns still goes through
the same domain validation as manual modelling.  These tests pin that contract
with a scripted provider, so no network or API key is needed.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from onto_platform.errors import ConnectorError, ValidationError
from onto_platform.infrastructure.llm.echo import EchoLLMProvider
from onto_platform.interfaces.api import build_handler
from tests.conftest import make_platform, seed_workspace

GOOD_DRAFT = {
    "objects": [
        {"name": "TollStation", "description": "收费站",
         "properties": [
             {"name": "name", "type": "string", "description": "站名"},
             {"name": "status", "type": "string", "description": "free/congested"},
         ]},
        {"name": "Vehicle", "description": "车辆",
         "properties": [
             {"name": "plate", "type": "string", "description": "车牌"},
             {"name": "vehicle_type", "type": "text", "description": "车型"},
         ]},
    ],
    "relations": [
        {"name": "passes", "source": "Vehicle", "target": "TollStation",
         "description": "车辆通过收费站", "cardinality": "many_to_many"},
    ],
    "vocabulary": [
        {"term": "收费站", "concept": "TollStation", "kind": "object",
         "aliases": ["Toll Station"]},
        {"term": "拥堵", "concept": "TollStation.status", "kind": "property",
         "aliases": ["排队"]},
    ],
}


def scripted(payload: dict, fenced: bool = False) -> EchoLLMProvider:
    body = json.dumps(payload, ensure_ascii=False)
    if fenced:
        body = "```json\n" + body + "\n```"
    return EchoLLMProvider([body])


def draft_workspace(platform, name: str = "建模测试") -> str:
    return platform.workspaces.create(name, actor="admin")["id"]


# ----------------------------------------------------------------------
# use case
# ----------------------------------------------------------------------


def test_preview_normalizes_draft_and_persists_nothing(platform):
    wid = draft_workspace(platform)
    result = platform.ontologies.draft(
        wid, "admin", "收费站有车辆通过，车辆缴纳通行费", provider=scripted(GOOD_DRAFT))

    assert result["applied"] is False
    assert {o["name"] for o in result["draft"]["objects"]} == {"TollStation", "Vehicle"}
    assert [r["name"] for r in result["draft"]["relations"]] == ["passes"]
    assert {v["term"] for v in result["draft"]["vocabulary"]} == {"收费站", "拥堵"}
    # type alias normalized, preview must not persist anything
    vehicle = next(o for o in result["draft"]["objects"] if o["name"] == "Vehicle")
    assert next(p for p in vehicle["properties"] if p["name"] == "vehicle_type")["type"] == "string"
    assert platform.ctx.repo.list("ontology", wid) == []


def test_apply_creates_ontology_with_objects_relations_terms(platform):
    wid = draft_workspace(platform)
    result = platform.ontologies.draft(
        wid, "admin", "收费站与车辆", apply=True, provider=scripted(GOOD_DRAFT))

    assert result["applied"] is True
    ontology = result["ontology"]
    assert set(ontology["objects"]) == {"TollStation", "Vehicle"}
    assert set(ontology["relations"]) == {"passes"}
    assert set(ontology["vocabulary"]) == {"收费站", "拥堵"}
    assert ontology["lifecycle"] == "draft"
    # and it is persisted / readable through the normal read path
    assert set(platform.ontologies.get(wid, "admin")["objects"]) == {"TollStation", "Vehicle"}
    assert platform.ontologies.validate(wid, "admin") == []


def test_invalid_elements_are_dropped_with_warnings(platform):
    wid = draft_workspace(platform)
    bad = {
        "objects": [
            {"name": "车辆", "properties": [{"name": "plate", "type": "string"}]},
            {"name": "1Bad", "properties": []},
            {"name": "Pass", "description": "通行记录",
             "properties": [{"name": "fee", "type": "float"}]},
        ],
        "relations": [
            {"name": "owns", "source": "Pass", "target": "Ghost"},
            {"name": "happens_on", "source": "Pass", "target": "Pass"},
        ],
        "vocabulary": [
            {"term": "幽灵", "concept": "Ghost", "kind": "object"},
            {"term": "费用", "concept": "Pass.missing", "kind": "property"},
            {"term": "通行", "concept": "happens_on", "kind": "relation"},
        ],
    }
    result = platform.ontologies.draft(
        wid, "admin", "只有通行记录", apply=True, provider=scripted(bad))

    draft = result["draft"]
    assert [o["name"] for o in draft["objects"]] == ["Pass"]
    assert draft["objects"][0]["properties"][0]["type"] == "number"  # float -> number
    assert [r["name"] for r in draft["relations"]] == ["happens_on"]
    assert [v["term"] for v in draft["vocabulary"]] == ["通行"]
    assert len(result["warnings"]) == 5, result["warnings"]
    assert set(result["ontology"]["objects"]) == {"Pass"}


def test_apply_extends_existing_draft_ontology(platform):
    wid = draft_workspace(platform)
    platform.ontologies.create(wid, "traffic", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        properties=[{"name": "name", "type": "string"}])

    result = platform.ontologies.draft(
        wid, "admin", "车辆通过收费站", apply=True, provider=scripted(GOOD_DRAFT))

    assert result["applied"] is True
    ontology = platform.ontologies.get(wid, "admin")
    assert set(ontology["objects"]) == {"TollStation", "Vehicle"}
    # existing property survives, parsed property is added
    assert set(ontology["objects"]["TollStation"]["properties"]) == {"name", "status"}
    assert [r["name"] for r in ontology["relations"].values()] == ["passes"]


def test_code_fenced_json_is_accepted(platform):
    wid = draft_workspace(platform)
    result = platform.ontologies.draft(
        wid, "admin", "收费站", provider=scripted(GOOD_DRAFT, fenced=True))
    assert {o["name"] for o in result["draft"]["objects"]} == {"TollStation", "Vehicle"}


def test_no_provider_is_a_clean_validation_error(platform):
    wid = draft_workspace(platform)
    with pytest.raises(ValidationError, match="no LLM provider configured"):
        platform.ontologies.draft(wid, "admin", "收费站")


def test_empty_text_is_rejected(platform):
    wid = draft_workspace(platform)
    with pytest.raises(ValidationError, match="text is required"):
        platform.ontologies.draft(wid, "admin", "   ", provider=scripted(GOOD_DRAFT))


def test_non_json_llm_reply_maps_to_connector_error(platform):
    wid = draft_workspace(platform)
    provider = EchoLLMProvider(["抱歉，我无法完成这个请求。"])
    with pytest.raises(ConnectorError, match="not a JSON object"):
        platform.ontologies.draft(wid, "admin", "收费站", provider=provider)


def test_llm_transport_failure_maps_to_connector_error(platform):
    class Boom:
        def complete(self, messages):
            raise RuntimeError("connection refused")

    wid = draft_workspace(platform)
    with pytest.raises(ConnectorError, match="LLM request failed"):
        platform.ontologies.draft(wid, "admin", "收费站", provider=Boom())


def test_published_ontology_rejects_apply(platform):
    wid = seed_workspace(platform)  # conftest seeds a *published* ontology
    preview = platform.ontologies.draft(
        wid, "admin", "车辆通过收费站", provider=scripted(GOOD_DRAFT))
    assert preview["applied"] is False  # previewing a published world is fine
    with pytest.raises(ValidationError, match="immutable"):
        platform.ontologies.draft(
            wid, "admin", "车辆通过收费站", apply=True, provider=scripted(GOOD_DRAFT))


def test_apply_exact_preview_without_a_second_llm_round(platform):
    """The UI previews once and applies what the user approved: passing the
    previewed draft back must not call the model again."""
    wid = draft_workspace(platform)
    preview = platform.ontologies.draft(
        wid, "admin", "收费站与车辆", provider=scripted(GOOD_DRAFT))

    class Exploding:
        def complete(self, messages):
            raise AssertionError("LLM must not be called again for an approved draft")

    applied = platform.ontologies.draft(
        wid, "admin", "", apply=True, provider=Exploding(), draft=preview["draft"])

    assert applied["applied"] is True
    assert {o["name"] for o in applied["draft"]["objects"]} == {"TollStation", "Vehicle"}
    assert set(applied["ontology"]["objects"]) == {"TollStation", "Vehicle"}


def test_apply_exact_preview_still_validates(platform):
    wid = draft_workspace(platform)
    with pytest.raises(ValidationError, match="draft must be an object"):
        platform.ontologies.draft(wid, "admin", "", apply=True, draft=["nope"])
    result = platform.ontologies.draft(
        wid, "admin", "", apply=True,
        draft={"objects": [{"name": "Pass", "properties": []}],
               "relations": [{"name": "ghost", "source": "Pass", "target": "Ghost"}],
               "vocabulary": []})
    assert [r["name"] for r in result["draft"]["relations"]] == []
    assert result["warnings"]


def test_draft_is_audited(platform):
    wid = draft_workspace(platform)
    platform.ontologies.draft(
        wid, "admin", "收费站与车辆", apply=True, provider=scripted(GOOD_DRAFT))
    actions = [entry["action"] for entry in platform.ctx.audit.list(wid, limit=20)]
    assert "ontology.draft" in actions


# ----------------------------------------------------------------------
# HTTP adapter
# ----------------------------------------------------------------------


@pytest.fixture()
def http(platform):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(platform))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", platform
    httpd.shutdown()


def _post(base, path, body):
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_draft_preview_then_apply(http):
    base, platform = http
    wid = draft_workspace(platform)
    script = {"llm": {"script": [json.dumps(GOOD_DRAFT, ensure_ascii=False)]}}

    status, body = _post(base, f"/api/workspaces/{wid}/ontology/draft",
                         {"user": "admin", "text": "收费站", **script})
    assert status == 200 and body["applied"] is False
    assert {o["name"] for o in body["draft"]["objects"]} == {"TollStation", "Vehicle"}

    status, body = _post(base, f"/api/workspaces/{wid}/ontology/draft",
                         {"user": "admin", "text": "收费站", "apply": True, **script})
    assert status == 200 and body["applied"] is True
    assert set(body["ontology"]["objects"]) == {"TollStation", "Vehicle"}

    # a viewer cannot draft (editor role required)
    status, body = _post(base, f"/api/workspaces/{wid}/ontology/draft",
                         {"user": "mallory", "text": "收费站", **script})
    assert status == 403


def test_http_draft_without_provider_is_400(http):
    base, platform = http
    wid = draft_workspace(platform)
    status, body = _post(base, f"/api/workspaces/{wid}/ontology/draft",
                         {"user": "admin", "text": "收费站"})
    assert status == 400 and "LLM provider" in body["error"]
