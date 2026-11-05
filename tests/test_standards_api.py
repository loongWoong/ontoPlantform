"""REST wiring for standard-format adapter plugins (T3).

Covers the external contract of docs/standards-plugin-implementation-plan-v1.md
§2.5:

* ``GET /standards/adapters`` lists every discovered adapter with its health
  (``status``/``problems``), not just the happy path;
* ``GET /standards/export`` streams the *artifact bytes* with the adapter's
  own media type and a download filename - the response must never be wrapped
  in JSON, otherwise every consumer needs the plugin SDK to read it;
* errors keep the platform taxonomy: unknown adapter 404, unsupported format
  400, missing adapter parameter 400, no published ontology 404.

The deterministic/semantic-red-line cross-check of the artifacts themselves
lives in the adapter tests (tests/test_standards_export.py) and in T4's
contract suite; here we only prove the bytes really reach a socket.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from onto_platform.application.common import published_ontology_row
from onto_platform.application.standards_usecases import StandardsUseCases
from onto_platform.semantic.registry import PluginRegistry
from onto_platform.semantic.spi import ADAPTER_API_VERSION, AdapterManifest
from tests.conftest import http_get


class FailingAdapter:
    """A registered plugin whose ``export`` always raises (decision D7)."""

    manifest = AdapterManifest(
        name="failing-adapter",
        version="9.9.9",
        api_version=ADAPTER_API_VERSION,
        capabilities=("export",),
        formats_out=("text/turtle",),
        description="test double that always fails",
    )

    def export(self, ctx, request):
        raise RuntimeError("plugin exploded")


def use_private_registry(platform, *adapters) -> PluginRegistry:
    """Point the seeded platform's standards facade at a private registry."""
    registry = PluginRegistry()
    for adapter in adapters:
        registry.register(adapter)
    platform.standards = StandardsUseCases(platform.ctx, registry=registry)
    return registry


def get_raw(base: str, path: str) -> tuple[int, dict, bytes]:
    """GET without JSON decoding: (status, headers, raw body)."""
    try:
        with urllib.request.urlopen(base + path) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _export_path(wid: str, query: str) -> str:
    return f"/api/workspaces/{wid}/standards/export?{query}&user=admin"


def test_adapters_lists_builtin_plugins(http_server):
    base, wid = http_server
    status, body = http_get(
        base, f"/api/workspaces/{wid}/standards/adapters?user=admin")

    assert status == 200
    adapters = {row["name"]: row for row in body["adapters"]}
    assert {"owl-rdf", "skos"} <= set(adapters)

    owl = adapters["owl-rdf"]
    assert owl["status"] == "ready"
    assert owl["problems"] == []
    assert owl["capabilities"] == ["export"]
    assert owl["formats_out"] == ["text/turtle"]
    assert owl["api_version"] == "1.0"
    # v1 policy: adapters are read-only and offline
    assert owl["permissions"] == {"network": False, "write": False}


def test_export_owl_rdf_returns_turtle_bytes(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=owl-rdf&format=text/turtle"))

    assert status == 200
    assert headers["Content-Type"].startswith("text/turtle")
    assert "charset=utf-8" in headers["Content-Type"].lower()
    disposition = headers["Content-Disposition"]
    assert "attachment" in disposition
    assert disposition.endswith('.ttl"')

    text = body.decode("utf-8")
    assert text.startswith("@prefix")
    for fragment in ("rdf:type", "owl:Class", "owl:ObjectProperty",
                     "owl:DatatypeProperty", "rdfs:domain", "rdfs:range"):
        assert fragment in text
    assert "Vehicle" in text and "passes" in text
    # semantic red line: runtime constraints are not OWL axioms
    assert "owl:hasKey" not in text
    assert "owl:cardinality" not in text


def test_export_skos_returns_concept_scheme(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=skos&format=text/turtle"))

    assert status == 200
    assert headers["Content-Type"].startswith("text/turtle")
    assert headers["Content-Disposition"].endswith('.skos.ttl"')
    text = body.decode("utf-8")
    assert "skos:ConceptScheme" in text
    assert "skos:Concept" in text
    assert "skos:prefLabel" in text


def test_format_defaults_to_turtle_when_omitted(http_server):
    base, wid = http_server
    status, headers, body = get_raw(base, _export_path(wid, "adapter=owl-rdf"))
    assert status == 200
    assert headers["Content-Type"].startswith("text/turtle")
    assert body


def test_unknown_adapter_maps_to_404(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=does-not-exist"))

    assert status == 404
    payload = json.loads(body)
    assert payload["type"] == "AdapterNotFound"
    assert payload["code"] == "not_found"


def test_unsupported_format_maps_to_400(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=owl-rdf&format=application/rdf%2Bxml"))

    assert status == 400
    payload = json.loads(body)
    assert payload["type"] == "AdapterError"
    assert "supported formats" in payload["error"]


def test_missing_adapter_parameter_maps_to_400(http_server):
    base, wid = http_server
    status, headers, body = get_raw(base, _export_path(wid, "format=text/turtle"))

    assert status == 400
    payload = json.loads(body)
    assert payload["type"] == "ValidationError"
    assert "adapter" in payload["error"]


def test_export_without_published_ontology_maps_to_404(http_server,
                                                       empty_workspace):
    base, _ = http_server
    status, headers, body = get_raw(
        base, _export_path(empty_workspace, "adapter=owl-rdf"))

    assert status == 404
    payload = json.loads(body)
    assert payload["code"] == "not_found"
    assert "no published ontology" in payload["error"]


def test_export_requires_workspace_membership(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, f"/api/workspaces/{wid}/standards/export?adapter=owl-rdf"
              f"&user=mallory")
    assert status == 403


def test_export_is_audited(platform, workspace_id):
    artifact = platform.standards.export(
        workspace_id, "admin", "owl-rdf", "text/turtle")

    events = [entry for entry in platform.ctx.audit.list(workspace_id,
                                                         limit=50)
              if entry["action"] == "standards.export"]
    assert events
    event = events[0]
    assert event["actor"] == "admin"
    assert event["target"] == "owl-rdf"
    assert event["detail"]["format"] == "text/turtle"
    assert event["detail"]["bytes"] == len(artifact["content"])
    assert event["detail"]["version"] == 1
    assert event["detail"]["schema_fingerprint"]


def test_export_metadata_carries_provenance(platform, workspace_id):
    artifact = platform.standards.export(
        workspace_id, "admin", "owl-rdf", "text/turtle")
    metadata = artifact["metadata"]
    published = published_ontology_row(platform.ctx, workspace_id)

    assert metadata["workspace_id"] == workspace_id
    assert metadata["ontology_version"] == 1
    assert metadata["schema_fingerprint"] == published["schema_fingerprint"]
    assert metadata["schema_fingerprint"]
    assert metadata["adapter_version"] == "0.1.0"
    assert metadata["actor"] == "admin"
    # the adapter's own metadata is preserved, not replaced
    assert metadata["adapter"] == "owl-rdf"
    assert metadata["terms"] > 0
    # determinism: no wall-clock field in the export contract
    assert "ts" not in metadata and "timestamp" not in metadata


def test_export_headers_expose_provenance(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=owl-rdf&format=text/turtle"))

    assert status == 200
    assert headers["X-Adapter-Name"] == "owl-rdf"
    assert headers["X-Adapter-Version"] == "0.1.0"
    assert headers["X-Ontology-Version"] == "1"
    assert headers["X-Schema-Fingerprint"]
    assert "charset=utf-8" in headers["Content-Type"].lower()
    assert body


def test_export_accepts_architecture_alias(http_server):
    base, wid = http_server
    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=onto-standards-owl"))

    assert status == 200
    # the header names the canonical plugin, even when addressed by alias
    assert headers["X-Adapter-Name"] == "owl-rdf"
    assert "owl:Class" in body.decode("utf-8")


def test_adapter_exception_maps_to_502_and_is_audited(http_server, platform):
    base, wid = http_server
    use_private_registry(platform, FailingAdapter())

    status, headers, body = get_raw(
        base, _export_path(wid, "adapter=failing-adapter&format=text/turtle"))

    assert status == 502
    payload = json.loads(body)
    assert payload["type"] == "AdapterInvocationError"
    assert payload["code"] == "plugin_failure"
    assert payload["retryable"] is False
    assert "plugin exploded" in payload["error"]

    events = [entry for entry in platform.ctx.audit.list(wid, limit=50)
              if entry["action"] == "standards.export.failed"]
    assert events
    event = events[0]
    assert event["actor"] == "admin"
    assert event["target"] == "failing-adapter"
    assert event["detail"]["error"] == "RuntimeError"
    assert "plugin exploded" in event["detail"]["message"]


def test_standards_absent_maps_to_404(http_server, platform, monkeypatch):
    base, wid = http_server
    monkeypatch.setattr(platform, "standards", None)

    status, _, body = get_raw(base, _export_path(wid, "adapter=owl-rdf"))
    assert status == 404
    payload = json.loads(body)
    assert payload["type"] == "NotFoundError"
    assert "not available" in payload["error"]

    status, _, body = get_raw(
        base, f"/api/workspaces/{wid}/standards/adapters?user=admin")
    assert status == 404
    assert json.loads(body)["code"] == "not_found"
