"""Independent contract & boundary verification for standard-format adapters (T4).

This module verifies the *published* P0 contract
(``docs/standards-plugin-implementation-plan-v1.md`` §2.5/§5/§7 and
``docs/standards-plugin-architecture.md`` §2.5) from the outside:

* discovery/manifest contract: every adapter the registry exposes is usable,
  declares ``export``, advertises the format it actually returns, and returns
  non-empty ``bytes``;
* determinism: the same published ontology + IRI policy always exports the
  same bytes (repeat calls and independently rebuilt contexts);
* semantic red line: the OWL artifact carries structural axioms only
  (``owl:Class``/``ObjectProperty``/``DatatypeProperty``) and never dresses
  runtime constraints up as OWL axioms or SHACL targets;
* dependency boundary: the ``semantic`` package imports no platform internals
  (bootstrap / runtime gateway / infrastructure) and no third-party RDF
  libraries, and the core distribution still ships ``dependencies = []``;
* end-to-end: the real HTTP socket serves the registry listing and artifact
  bytes, maps errors to the platform taxonomy, and 404s an empty workspace;
* optional interop: when ``rdflib`` is installed the exported Turtle is
  re-parsed; without it the check is skipped (never installed by the test).

The tests build their own ontology instead of reusing the developer fixtures'
assertions, so a regression in the adapters cannot be hidden by a shared
helper.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import pytest

from onto_platform.domain.ontology.model import (
    ObjectTypeSpec,
    Ontology,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from onto_platform.semantic import (
    ADAPTER_API_VERSION,
    AdapterError,
    AdapterManifest,
    AdapterNotFound,
    ExportRequest,
    PluginRegistry,
    build_context,
    default_registry,
    manifest_problems,
    reset_default_registry,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SEMANTIC_DIR = REPO_ROOT / "src" / "onto_platform" / "semantic"

#: Axioms/features that must never appear in a P0 OWL export: they would assert
#: semantics the platform does not have (identity as ``sameAs``, runtime
#: constraints as OWL restrictions) or leak SHACL into the OWL artifact.
SEMANTIC_RED_LINE = (
    "owl:hasKey",
    "owl:cardinality",
    "owl:minCardinality",
    "owl:maxCardinality",
    "owl:FunctionalProperty",
    "owl:someValuesFrom",
    "owl:allValuesFrom",
    "sh:target",
    "owl:Restriction",
)

#: The semantic layer must stay a leaf: no composition root, no runtime
#: gateway, no infrastructure, no third-party RDF stack.
FORBIDDEN_SEMANTIC_REFERENCES = (
    "onto_platform.bootstrap",
    "from ..bootstrap",
    "KernelRuntimeGateway",
    "onto_platform.infrastructure",
    "from ..infrastructure",
    "infrastructure.runtime",
    "import rdflib",
    "import pyshacl",
)


def _contract_ontology() -> Ontology:
    """A small but structurally complete TBox (classes, properties, relation)."""
    ontology = Ontology(
        id="ont-qa-contract",
        workspace_id="ws-qa-contract",
        name="qa-traffic",
        version=3,
        schema_fingerprint="fp-qa-contract-0001",
        description="QA contract fixture ontology",
    )
    ontology.upsert_object(ObjectTypeSpec(
        name="Vehicle",
        description="车辆",
        identity=["plate"],
        properties={
            "plate": PropertySpec(name="plate", type="string"),
            "balance": PropertySpec(name="balance", type="number"),
            "year": PropertySpec(name="year", type="integer"),
            "active": PropertySpec(name="active", type="boolean"),
            "seenAt": PropertySpec(name="seenAt", type="datetime"),
        },
    ))
    ontology.upsert_object(ObjectTypeSpec(
        name="TollStation",
        description="收费站",
        identity=["code"],
        properties={"code": PropertySpec(name="code", type="string")},
    ))
    ontology.upsert_relation(RelationSpec(
        name="passes",
        source="Vehicle",
        target="TollStation",
        inverse="passedBy",
    ))
    ontology.upsert_relation(RelationSpec(
        name="passedBy",
        source="TollStation",
        target="Vehicle",
        inverse="passes",
    ))
    ontology.add_term(VocabularyTerm(
        term="车辆", concept="Vehicle", aliases=["车", "vehicle"]))
    ontology.add_term(VocabularyTerm(
        term="收费站", concept="TollStation", aliases=["站点"]))
    ontology.add_term(VocabularyTerm(
        term="拥堵", concept="TollStation.code", kind="property"))
    return ontology


@pytest.fixture()
def registry():
    """A freshly discovered process registry (no leftover plugins from tests)."""
    reset_default_registry()
    yield default_registry()
    reset_default_registry()


@pytest.fixture()
def contract_context():
    """The read-only adapter context for the contract ontology."""
    return build_context(_contract_ontology(), "qa-verifier")


def _export(registry, context, name: str, media_type: str = "text/turtle"):
    adapter = registry.get(name)
    return adapter, adapter.export(context, ExportRequest(format=media_type))


# ---------------------------------------------------------------------------
# a) discovery / manifest / artifact contract
# ---------------------------------------------------------------------------

def test_discovered_adapters_expose_a_usable_export_contract(
        registry, contract_context):
    names = registry.names()
    assert {"owl-rdf", "skos"} <= set(names), names

    rows = registry.manifests()
    assert [row["name"] for row in rows] == sorted(row["name"] for row in rows)

    for row in rows:
        adapter = registry.get(row["name"])
        manifest = adapter.manifest
        assert isinstance(manifest, AdapterManifest)
        # discovery reports only fully usable plugins as ready
        assert row["status"] == "ready"
        assert row["problems"] == []
        assert manifest_problems(manifest) == []
        assert "export" in manifest.capabilities
        assert manifest.formats_out

    for name in ("owl-rdf", "skos"):
        adapter, artifact = _export(registry, contract_context, name)
        assert artifact.media_type in adapter.manifest.formats_out
        assert isinstance(artifact.content, bytes)
        assert artifact.content, f"{name} returned an empty artifact"
        assert artifact.filename, f"{name} did not name its artifact"


# ---------------------------------------------------------------------------
# b) determinism
# ---------------------------------------------------------------------------

def test_export_is_byte_identical_for_the_same_ontology_and_iri_policy(registry):
    first = build_context(_contract_ontology(), "qa-verifier")
    # A second, independently built context must not change a single byte:
    # determinism is part of the serving contract, not an implementation detail.
    second = build_context(_contract_ontology(), "qa-verifier")

    for name in ("owl-rdf", "skos"):
        adapter = registry.get(name)
        request = ExportRequest(format="text/turtle")
        once = adapter.export(first, request)
        twice = adapter.export(first, request)
        rebuilt = adapter.export(second, request)

        assert once.content == twice.content, f"{name} is not repeatable"
        assert once.content == rebuilt.content, f"{name} depends on context identity"
        assert once.media_type == twice.media_type == rebuilt.media_type


# ---------------------------------------------------------------------------
# c) semantic red line
# ---------------------------------------------------------------------------

def test_owl_export_contains_structure_but_no_runtime_axioms(registry,
                                                            contract_context):
    _, artifact = _export(registry, contract_context, "owl-rdf")
    text = artifact.content.decode("utf-8")

    assert "owl:Class" in text
    assert "owl:ObjectProperty" in text
    assert "owl:DatatypeProperty" in text

    for forbidden in SEMANTIC_RED_LINE:
        assert forbidden not in text, (
            f"OWL artifact violates the semantic red line: {forbidden}")


# ---------------------------------------------------------------------------
# d) dependency boundary
# ---------------------------------------------------------------------------

def test_semantic_package_does_not_reach_into_platform_internals():
    modules = sorted(SEMANTIC_DIR.glob("*.py"))
    assert modules, f"no semantic modules found under {SEMANTIC_DIR}"

    for module in modules:
        source = module.read_text(encoding="utf-8")
        for reference in FORBIDDEN_SEMANTIC_REFERENCES:
            assert reference not in source, (
                f"{module.name} must not reference {reference!r}")


def test_core_distribution_keeps_zero_runtime_dependencies():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]

    assert project["dependencies"] == []
    assert project["optional-dependencies"]["interop"], "interop extra is missing"
    interop = project["optional-dependencies"]["interop"]
    package_names = {
        requirement.split(";")[0].split(">=")[0].split("==")[0].split("[")[0].strip()
        for requirement in interop
    }
    assert "rdflib" in package_names, interop


# ---------------------------------------------------------------------------
# e) end-to-end over a real socket
# ---------------------------------------------------------------------------

def _raw_get(base: str, path: str):
    """GET returning ``(status, headers, body bytes)`` without JSON decoding."""
    try:
        with urllib.request.urlopen(base + path, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _export_path(workspace_id: str, adapter: str, media_type: str = "") -> str:
    path = (
        f"/api/workspaces/{workspace_id}/standards/export"
        f"?adapter={adapter}"
    )
    if media_type:
        path += f"&format={urllib.parse.quote(media_type, safe='')}"
    return path


def test_http_adapter_listing_reflects_the_plugin_registry(http_server):
    base, workspace_id = http_server
    status, headers, body = _raw_get(
        base, f"/api/workspaces/{workspace_id}/standards/adapters")

    assert status == 200
    payload = json.loads(body)
    rows = {row["name"]: row for row in payload["adapters"]}
    assert {"owl-rdf", "skos"} <= set(rows)
    for name in ("owl-rdf", "skos"):
        assert rows[name]["status"] == "ready"
        assert rows[name]["problems"] == []


def test_http_owl_export_returns_a_turtle_document(http_server):
    base, workspace_id = http_server
    path = _export_path(workspace_id, "owl-rdf", "text/turtle")
    status, headers, body = _raw_get(base, path)

    assert status == 200
    assert "text/turtle" in headers.get("Content-Type", "")
    disposition = headers.get("Content-Disposition", "")
    assert "attachment" in disposition.lower()
    assert "filename=" in disposition

    text = body.decode("utf-8")
    assert "@prefix" in text
    assert "rdf:type" in text
    assert "owl:Class" in text
    for forbidden in SEMANTIC_RED_LINE:
        assert forbidden not in text, (
            f"HTTP OWL export violates the semantic red line: {forbidden}")

    # DoD 3: two consecutive exports of the same published version are
    # byte-identical, not merely equivalent.
    again_status, again_headers, again_body = _raw_get(base, path)
    assert again_status == 200
    assert again_body == body


def test_http_skos_export_returns_a_concept_scheme(http_server):
    base, workspace_id = http_server
    status, headers, body = _raw_get(
        base, _export_path(workspace_id, "skos", "text/turtle"))

    assert status == 200
    assert "text/turtle" in headers.get("Content-Type", "")
    assert "skos:ConceptScheme" in body.decode("utf-8")


def test_http_unknown_adapter_is_404_in_the_current_implementation(http_server):
    """Plan §2.5 says 400 for an unknown adapter; the shipped code maps it to
    404 via ``AdapterNotFound`` (a ``NotFoundError``).  Lock the observable
    behaviour here and let the report flag the plan/document mismatch."""
    base, workspace_id = http_server
    status, headers, body = _raw_get(
        base, _export_path(workspace_id, "qa-no-such-adapter"))

    assert status == 404
    payload = json.loads(body)
    assert payload["type"] == "AdapterNotFound"
    assert payload["code"] == "not_found"


def test_http_unsupported_format_is_400(http_server):
    base, workspace_id = http_server
    status, headers, body = _raw_get(
        base, _export_path(workspace_id, "owl-rdf", "application/rdf+xml"))

    assert status == 400
    payload = json.loads(body)
    assert payload["type"] == "AdapterError"
    assert "supported formats" in payload["error"]


def test_http_export_without_a_published_ontology_is_404(http_server,
                                                         empty_workspace):
    base, _ = http_server
    status, headers, body = _raw_get(
        base, _export_path(empty_workspace, "owl-rdf"))

    assert status == 404
    payload = json.loads(body)
    assert payload["code"] == "not_found"
    assert "published ontology" in payload["error"]


# ---------------------------------------------------------------------------
# f) optional rdflib round-trip (interop extra; never installed by tests)
# ---------------------------------------------------------------------------

def test_owl_artifact_parses_with_rdflib_when_available(registry,
                                                        contract_context):
    if importlib.util.find_spec("rdflib") is None:
        pytest.skip("rdflib not installed (pyproject interop extra)")

    from rdflib import Graph
    from rdflib.namespace import OWL, RDF

    _, artifact = _export(registry, contract_context, "owl-rdf")
    graph = Graph().parse(data=artifact.content.decode("utf-8"), format="turtle")

    assert len(graph) > 0
    assert any(True for _ in graph.triples((None, RDF.type, OWL.Class)))


# ---------------------------------------------------------------------------
# g) registry robustness: one broken plugin must not poison the rest
# ---------------------------------------------------------------------------

class _BrokenManifestAdapter:
    """A plugin whose manifest violates the contract (no capabilities)."""

    manifest = AdapterManifest(
        name="qa-broken",
        version="0.1.0",
        api_version=ADAPTER_API_VERSION,
        capabilities=(),
        formats_out=(),
    )


def test_registry_isolates_a_broken_plugin_and_still_serves_ready_ones():
    registry = PluginRegistry(
        entry_points={"qa-broken": lambda: _BrokenManifestAdapter()})
    loaded = registry.discover()

    assert {"owl-rdf", "skos"} <= set(loaded)
    assert registry.names() == ["owl-rdf", "skos"]

    rows = {row["name"]: row for row in registry.manifests()}
    assert rows["qa-broken"]["status"] == "invalid"
    assert rows["qa-broken"]["problems"]
    for name in ("owl-rdf", "skos"):
        assert rows[name]["status"] == "ready"
        assert rows[name]["problems"] == []

    # an invalid plugin is a platform error, not "not found"
    with pytest.raises(AdapterError) as invalid:
        registry.get("qa-broken")
    assert "not usable" in str(invalid.value)

    with pytest.raises(AdapterNotFound):
        registry.get("qa-never-registered")
