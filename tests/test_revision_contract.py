"""Phase 2.5: revision lineage, schema fingerprint, no half-installed runtime."""

from __future__ import annotations

import json

import pytest

from onto_platform.bootstrap import PlatformConfig, build_platform

from conftest import http_post, make_platform, seed_workspace


def test_new_revision_increments_version_and_links_parent(platform):
    wid = seed_workspace(platform)
    published = platform.ontologies.get(wid, "admin")
    revised = platform.ontologies.revise(wid, "admin")
    assert revised["version"] == published["version"] + 1
    assert revised["schema_version"] == published["schema_version"] + 1
    assert revised["parent_version"] == published["version"]
    assert revised["lifecycle"] == "draft"
    old = platform.ontologies.get(wid, "admin")
    assert old["id"] == revised["id"]
    rows = platform.ctx.repo.list("ontology", wid)
    deprecated = [r for r in rows if r["lifecycle"] == "deprecated"]
    assert len(deprecated) == 1
    assert deprecated[0]["version"] == published["version"]


def test_revision_endpoint_creates_a_draft(platform, http_server):
    base, wid = http_server
    status, body = http_post(base, f"/api/workspaces/{wid}/ontology/revisions")
    assert status == 200, body
    assert body["lifecycle"] == "draft"
    assert body["parent_version"] == 1


def test_publish_writes_schema_fingerprint(platform):
    wid = platform.workspaces.create("fingerprint", actor="admin")["id"]
    platform.ontologies.create(wid, "fp", "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Station",
        [{"name": "code", "type": "string", "required": True}])
    result = platform.ontologies.publish(wid, "admin")
    fingerprint = result["ontology"]["schema_fingerprint"]
    assert fingerprint
    assert fingerprint == result["release"]["fingerprint"]
    # revision drafts do not pretend to have a released fingerprint
    revised = platform.ontologies.revise(wid, "admin")
    assert revised["schema_fingerprint"] == ""


def test_no_half_installed_runtime(platform):
    wid = seed_workspace(platform)
    before = platform.ctx.repo.get("deployment", wid, "current")
    published = platform.ontologies.get(wid, "admin")
    platform.ontologies.revise(wid, "admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        [{"name": "plate", "type": "string"},
         {"name": "balance", "type": "number"},
         {"name": "color", "type": "string"}])
    original = platform.ontologies.publish._publisher.publish

    def boom(*args, **kwargs):
        raise RuntimeError("kernel rejected the payload")

    platform.ontologies.publish._publisher.publish = boom
    try:
        with pytest.raises(RuntimeError):
            platform.ontologies.publish(wid, "admin")
    finally:
        platform.ontologies.publish._publisher.publish = original
    after = platform.ctx.repo.get("deployment", wid, "current")
    assert after == before
    current = platform.ontologies.get(wid, "admin")
    assert current["lifecycle"] == "draft"
    assert current["schema_fingerprint"] == ""
    deployed = platform.ctx.gateway_for(wid).describe()
    objects = deployed.get("objects") or {}
    if isinstance(objects, list):
        vehicle = next(o for o in objects if o.get("name") == "Vehicle")
    else:
        vehicle = objects.get("Vehicle", {})
    props = vehicle.get("properties") or {}
    names = set(props) if isinstance(props, dict) else {
        p.get("name") for p in props if isinstance(p, dict)}
    assert "color" not in names


def test_self_heal_detects_schema_fingerprint_mismatch(tmp_path):
    db = str(tmp_path / "platform.db")
    platform = build_platform(PlatformConfig(store="sqlite", db_path=db))
    wid = seed_workspace(platform)
    # the deployed Release is the runtime source of truth; tampering its
    # schema must be detected, not silently served
    deployment = platform.ctx.repo.get("deployment", wid, "current")
    release = platform.ctx.repo.get("release", wid, deployment["release_id"])
    tampered = json.loads(json.dumps(release))
    tampered["schema"]["description"] = "drifted after deployment"
    platform.ctx.repo.put("release", wid, tampered["id"], tampered)

    restarted = build_platform(PlatformConfig(store="sqlite", db_path=db))
    restarted.ctx.gateway_for(wid)  # rebuilds from the deployed release
    mismatches = [e for e in restarted.events.list(
        wid, event_type="schema.fingerprint_mismatch")]
    assert len(mismatches) == 1
    assert mismatches[0]["payload"]["expected"] != mismatches[0]["payload"]["actual"]
    metrics = restarted.metrics(wid, "admin")["indicators"]
    assert metrics["schema_fingerprint_mismatches"] == 1
