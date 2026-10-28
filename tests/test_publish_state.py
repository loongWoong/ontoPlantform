"""Phase 1.11 / O-8: publish-time ABox compliance against the new TBox."""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError
from onto_platform.ports.runtime import ObjectUpsert, SemanticDelta


def _published_vehicle(platform):
    wid = platform.workspaces.create("abox", actor="admin")["id"]
    platform.ontologies.create(wid, "abox", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string"}],
        identity=["plate"])
    platform.ontologies.publish(wid, "admin")
    platform.ctx.gateway_for(wid).apply_delta(SemanticDelta(upserts=(
        ObjectUpsert(type="Vehicle", id="V1",
                     properties={"plate": "A1", "status": "CONGESTED"}),)))
    return wid


def test_publish_rejects_narrowing_when_state_violates(platform):
    """A narrowed-but-compatible TBox still must fit the existing ABox (O-8)."""
    wid = _published_vehicle(platform)
    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string"},
         {"name": "color", "type": "string", "required": True}],
        identity=["plate"])
    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.publish(wid, "admin")
    message = str(excinfo.value)
    assert "runtime state" in message
    assert "Vehicle/V1" in message
    assert "color" in message
    # nothing was published and the draft is still editable
    assert platform.ontologies.get(wid, "admin")["lifecycle"] == "draft"


def test_publish_allows_additive_change(platform):
    wid = _published_vehicle(platform)
    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string"},
         {"name": "color", "type": "string"}],
        identity=["plate"])
    published = platform.ontologies.publish(wid, "admin")
    assert published["ontology"]["lifecycle"] == "published"
    obj = platform.ctx.gateway_for(wid).get_object("Vehicle/V1")
    assert obj["properties"]["plate"] == "A1"


def test_allow_breaking_override_is_audited(platform):
    wid = _published_vehicle(platform)
    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string"},
         {"name": "color", "type": "string", "required": True}],
        identity=["plate"])
    result = platform.ontologies.publish(wid, "admin", allow_breaking=True)
    assert result["ontology"]["lifecycle"] == "published"
    audits = [a for a in platform.ctx.audit.list(wid)
              if a["action"] == "ontology.publish_breaking"]
    assert audits and "Vehicle/V1" in audits[0]["detail"]["samples"]
