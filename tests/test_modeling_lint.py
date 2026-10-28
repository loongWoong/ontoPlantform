"""Phase 1.11: modeling lint (O-6/O-7) and datetime normalization."""

from __future__ import annotations

import pytest

from onto_platform.domain.intelligence.model import SemanticIndex
from onto_platform.domain.ontology.model import Ontology
from onto_platform.errors import ValidationError
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta


def _new_workspace(platform, name: str = "semantics") -> str:
    return platform.workspaces.create(name, actor="admin")["id"]


def test_lint_flags_duplicated_endpoint(platform):
    wid = _new_workspace(platform)
    platform.ontologies.create(wid, "toll", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        [{"name": "name", "type": "string", "required": True},
         {"name": "status", "type": "string", "enum": ["free", "busy"]}])
    platform.ontologies.upsert_object(
        wid, "admin", "TollTransaction",
        [{"name": "entry_station", "type": "string"},
         {"name": "hour", "type": "string"},
         {"name": "occurred_at", "type": "datetime"}])
    platform.ontologies.upsert_relation(
        wid, "admin", "enters", "TollTransaction", "TollStation")

    lint = platform.ontologies.lint(wid, "admin")
    codes = {w["code"] for w in lint["warnings"]}
    assert lint["ok"] is False
    assert "duplicated-endpoint" in codes
    assert "string-datetime" in codes
    subjects = {w["subject"] for w in lint["warnings"]}
    assert "TollTransaction.entry_station" in subjects
    assert "TollTransaction.hour" in subjects
    # lint never blocks publishing
    platform.ontologies.publish(wid, "admin")


def test_lint_endpoint_is_exposed_over_http(platform, http_server):
    from conftest import http_post

    base, _ = http_server
    wid = platform.workspaces.create("lint-http", actor="admin")["id"]
    platform.ontologies.create(wid, "http-lint", "admin")
    status, body = http_post(base, f"/api/workspaces/{wid}/ontology/lint")
    assert status == 200, body
    assert body["ok"] is True and body["warnings"] == []
    platform.ontologies.upsert_object(
        wid, "admin", "TrafficFlow", [{"name": "hour", "type": "string"}])
    status, body = http_post(base, f"/api/workspaces/{wid}/ontology/lint")
    assert status == 200, body
    assert body["ok"] is False
    assert {w["code"] for w in body["warnings"]} == {"string-datetime"}


def test_datetime_values_are_normalized_to_utc(platform):
    wid = _new_workspace(platform, "time")
    platform.ontologies.create(wid, "time", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Reading",
        [{"name": "name", "type": "string", "required": True},
         {"name": "measured_at", "type": "datetime"}])
    platform.ontologies.publish(wid, "admin")

    gateway = platform.ctx.gateway_for(wid)
    gateway.apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Reading", id="naive",
                     properties={"name": "a", "measured_at": "2026-05-06T07:07:00"}),
        ObjectUpsert(type="Reading", id="offset",
                     properties={"name": "b",
                                 "measured_at": "2026-05-06T15:07:00+08:00"}),
        ObjectUpsert(type="Reading", id="utc",
                     properties={"name": "c",
                                 "measured_at": "2026-05-06T07:07:00+00:00"}),
    )))
    values = {ref: gateway.get_object(f"Reading/{ref}")["properties"]["measured_at"]
              for ref in ("naive", "offset", "utc")}
    assert values["naive"] == values["offset"] == values["utc"] == \
        "2026-05-06T07:07:00+00:00"
    assert sorted(values.values()) == [values["naive"]] * 3


def test_invalid_datetime_is_rejected_at_the_write_boundary(platform):
    wid = _new_workspace(platform, "time2")
    platform.ontologies.create(wid, "time2", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Reading",
        [{"name": "name", "type": "string", "required": True},
         {"name": "measured_at", "type": "datetime"}])
    platform.ontologies.publish(wid, "admin")
    with pytest.raises(ValidationError):
        platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(upserts=(
            ObjectUpsert(type="Reading", id="bad",
                         properties={"name": "x", "measured_at": "not-a-time"}),)))


def test_agent_context_contains_enum_values(platform):
    wid = _new_workspace(platform, "context")
    platform.ontologies.create(wid, "context", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        [{"name": "code", "type": "string", "required": True},
         {"name": "status", "type": "string",
          "enum": ["free", "busy", "congested"], "temporal": "snapshot"},
         {"name": "valid_from", "type": "datetime"}],
        identity=["code"])
    ontology = Ontology.from_dict(platform.ontologies.get(wid, "admin"))
    text = SemanticIndex.from_ontology(ontology).context_text(ontology)
    assert "identified by code" in text
    assert "TollStation.status ∈ {free, busy, congested}" in text
    assert "temporal: snapshot" in text


def test_value_category_must_not_have_identity(platform):
    """O-12: value types are content-identified; no identity, no endpoints."""
    wid = _new_workspace(platform, "values")
    platform.ontologies.create(wid, "values", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Money",
        [{"name": "code", "type": "string", "required": True},
         {"name": "amount", "type": "number"}],
        category="value", identity=["code"])
    platform.ontologies.upsert_object(
        wid, "admin", "Invoice",
        [{"name": "no", "type": "string", "required": True}], identity=["no"])
    platform.ontologies.upsert_relation(
        wid, "admin", "priced_in", "Invoice", "Money")

    lint = platform.ontologies.lint(wid, "admin")
    codes = {w["code"] for w in lint["warnings"]}
    assert "value-with-identity" in codes
    assert "value-as-relation-endpoint" in codes
    with pytest.raises(ValidationError, match="must not declare an identity"):
        platform.ontologies.publish(wid, "admin")


def test_unit_mismatch_rejected(platform):
    """1.4: a projection cannot silently rescale units."""
    wid = _new_workspace(platform, "units")
    platform.ontologies.create(wid, "units", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Station",
        [{"name": "code", "type": "string", "required": True},
         {"name": "balance", "type": "number", "unit": "CNY"}],
        identity=["code"])
    platform.ontologies.publish(wid, "admin")
    source = platform.data.connect_source(
        wid, "admin", "src", "static",
        assets=[{"name": "rows", "config": {"rows": [
            {"code": "S1", "balance": 10}]}}])
    with pytest.raises(ValidationError) as excinfo:
        platform.data.create_projection(
            wid, "admin", "bad-unit", source["id"],
            object_mappings=[{"asset": "rows", "object_type": "Station",
                              "id_field": "code",
                              "fields": [{"source_field": "balance",
                                          "property": "balance",
                                          "unit": "USD"}]}])
    assert "unit" in str(excinfo.value)
    projection = platform.data.create_projection(
        wid, "admin", "good-unit", source["id"],
        object_mappings=[{"asset": "rows", "object_type": "Station",
                          "id_field": "code",
                          "fields": [{"source_field": "balance",
                                      "property": "balance",
                                      "unit": "CNY"}]}])
    assert projection["object_mappings"][0]["fields"][0]["unit"] == "CNY"
