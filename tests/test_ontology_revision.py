"""Phase 1.11 / 2.5: ontology revisions, narrowing matrix, vocabulary lint."""

from __future__ import annotations

import pytest

from onto_platform.errors import ValidationError

from conftest import seed_workspace


def _draft_world(platform, wid):
    platform.ontologies.create(wid, "world", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string",
          "enum": ["free", "congested"]}],
        identity=["plate"])
    platform.ontologies.validate(wid, "admin")
    return platform.ontologies.publish(wid, "admin")


def test_revision_starts_a_new_draft_and_deprecates_the_old(platform,
                                                            empty_workspace):
    wid = empty_workspace
    first = _draft_world(platform, wid)
    assert first["release"]["schema_version"] == 1

    revised = platform.ontologies.revise(wid, "admin")
    assert revised["version"] == 2
    assert revised["schema_version"] == 2
    assert revised["lifecycle"] == "draft"
    versions = {o["version"]: o["lifecycle"]
                for o in platform.ctx.repo.list("ontology", wid)}
    assert versions == {1: "deprecated", 2: "draft"}

    # the new draft is the active ontology: edits land there
    platform.ontologies.upsert_object(
        wid, "admin", "Hub", [{"name": "code", "type": "string", "required": True}],
        identity=["code"])
    published = platform.ontologies.publish(wid, "admin")
    assert published["release"]["schema_version"] == 2
    assert published["deployment"]["release_id"] == published["release"]["id"]


def test_narrowing_revision_is_rejected_with_migration_advice(platform,
                                                              empty_workspace):
    wid = empty_workspace
    _draft_world(platform, wid)
    platform.ontologies.revise(wid, "admin")
    # narrowing: drop an enum value that published data may already use
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string", "enum": ["free"]}],
        identity=["plate"])
    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.publish(wid, "admin")
    message = str(excinfo.value)
    assert "narrows" in message and "migration" in message
    assert "enum narrowed" in message
    # the failed publish left the previous deployment in place
    deployed = platform.releases.list(wid, "admin")["deployment"]
    assert deployed["schema_version"] == 1


def test_widening_revision_is_publishable(platform, empty_workspace):
    wid = empty_workspace
    _draft_world(platform, wid)
    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string", "required": True},
         {"name": "status", "type": "string"},  # enum widened to "any string"
         {"name": "odometer", "type": "number"}],
        identity=["plate"])
    published = platform.ontologies.publish(wid, "admin")
    assert published["release"]["schema_version"] == 2
    assert "odometer" in published["ontology"]["objects"]["Vehicle"]["properties"]


def test_release_fingerprint_identifies_the_compiled_schema(platform,
                                                           empty_workspace):
    wid = empty_workspace
    published = _draft_world(platform, wid)
    release = published["release"]
    assert len(release["fingerprint"]) == 16
    assert all(c in "0123456789abcdef" for c in release["fingerprint"])
    listed = platform.releases.list(wid, "admin")
    current = listed["deployment"]
    assert current["release_id"] == release["id"]
    assert listed["releases"][0]["fingerprint"] == release["fingerprint"]


def test_vocabulary_alias_must_be_unique(platform, empty_workspace):
    wid = empty_workspace
    platform.ontologies.create(wid, "world", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        [{"name": "code", "type": "string", "required": True}], identity=["code"])
    platform.ontologies.add_term(
        wid, "admin", "收费站", "TollStation", aliases=["station"])
    platform.ontologies.add_term(
        wid, "admin", "站点", "TollStation", aliases=["Station"])
    problems = platform.ontologies.validate(wid, "admin")
    assert any("station" in p.lower() and "unambiguous" in p
               for p in problems), problems
    with pytest.raises(ValidationError):
        platform.ontologies.publish(wid, "admin")
