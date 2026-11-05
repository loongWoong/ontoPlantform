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

C0+C1 completion (task D of docs/standards-plugin-completion-plan-v1.md) is
verified on top of that baseline: grouped manifest fields (with the legacy
flat keys kept), canonical/alias resolution with byte-identical exports,
capability-vs-method consistency, ``requires.ontoplatform`` compatibility,
explicit ABox/scope rejection, CJK/ASCII language tags, plugin exception
isolation (502 + ``standards.export.failed`` audit), provenance
headers/metadata without timestamps, frozen golden artifacts, and byte
determinism across separate Python processes with different
``PYTHONHASHSEED`` values.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import pytest

from onto_platform.application.common import published_ontology_row
from onto_platform.application.standards_usecases import StandardsUseCases
from onto_platform.domain.ontology.model import (
    ObjectTypeSpec,
    Ontology,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from onto_platform.errors import classify
from onto_platform.semantic import (
    ADAPTER_API_VERSION,
    CAPABILITY_METHODS,
    AdapterError,
    AdapterIncompatible,
    AdapterInvocationError,
    AdapterManifest,
    AdapterNotFound,
    Artifact,
    ExportRequest,
    PluginRegistry,
    adapter_contract_problems,
    build_context,
    default_registry,
    manifest_problems,
    platform_version,
    requires_compatible,
    requires_problems,
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
    """An unknown adapter keeps the platform not-found taxonomy (404).

    ``PluginRegistry.get`` raises ``AdapterNotFound`` (a ``NotFoundError``);
    the completion plan now documents 404 for an unknown adapter and 400 for
    a registered-but-unusable one, so this is the observable contract.
    """
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


# ---------------------------------------------------------------------------
# h) manifest contract: grouped architecture fields + legacy flat keys (D3)
# ---------------------------------------------------------------------------

def test_manifest_rows_carry_grouped_architecture_fields(registry):
    rows = {row["name"]: row for row in registry.manifests()}

    for name, alias in (("owl-rdf", "onto-standards-owl"),
                        ("skos", "onto-standards-skos")):
        row = rows[name]
        assert row["status"] == "ready"
        assert row["problems"] == []
        # architecture §2.2 grouped views, derived from the legacy flat keys
        assert row["formats"] == {
            "out": row["formats_out"], "in": row["formats_in"]}
        assert row["formats"]["out"] == ["text/turtle"]
        assert row["formats"]["in"] == []
        assert row["requires"] == {"ontoplatform": "", "extras": []}
        assert row["config_schema"] == {}
        assert row["aliases"] == [alias]
        # the manifest row is a plain JSON payload for API consumers
        json.dumps(row, ensure_ascii=False)

        adapter = registry.get(name)
        assert manifest_problems(adapter.manifest) == []
        assert requires_problems(adapter.manifest) == []
        assert requires_compatible(adapter.manifest) == (True, "")


def test_a_legacy_manifest_still_registers_with_grouped_defaults():
    """A pre-D3 plugin (no requires/config_schema/aliases) must still work."""

    class _LegacyAdapter:
        manifest = AdapterManifest(
            name="qa-legacy",
            version="0.1.0",
            api_version=ADAPTER_API_VERSION,
            capabilities=("export",),
            formats_out=("text/turtle",),
        )

        def export(self, ctx, request):
            return Artifact(content=b"legacy", media_type="text/turtle")

    registry = PluginRegistry()
    registry.register(_LegacyAdapter())
    row = next(row for row in registry.manifests()
               if row["name"] == "qa-legacy")

    assert row["status"] == "ready"
    assert row["formats"] == {"out": ["text/turtle"], "in": []}
    assert row["requires"] == {"ontoplatform": "", "extras": []}
    assert row["config_schema"] == {}
    assert row["aliases"] == []
    assert registry.get("qa-legacy") is not None


# ---------------------------------------------------------------------------
# i) canonical names + architecture aliases (decision D1)
# ---------------------------------------------------------------------------

def test_alias_resolution_keeps_canonical_names_and_identical_exports(
        registry, contract_context):
    assert registry.names() == ["owl-rdf", "skos"]

    for alias, canonical in (("onto-standards-owl", "owl-rdf"),
                             ("onto-standards-skos", "skos")):
        assert alias not in registry.names()
        by_alias = registry.get(alias)
        by_name = registry.get(canonical)
        assert by_alias is by_name
        assert by_alias.manifest.name == canonical
        assert by_alias.manifest.to_dict()["aliases"] == [alias]

        request = ExportRequest(format="text/turtle")
        assert (by_alias.export(contract_context, request).content
                == by_name.export(contract_context, request).content)

    with pytest.raises(AdapterNotFound):
        registry.get("onto-standards-unknown")


# ---------------------------------------------------------------------------
# j) capability vs. method consistency (G-02)
# ---------------------------------------------------------------------------

class _ClaimsValidateWithoutMethod:
    """A plugin whose manifest advertises a capability it does not implement."""

    manifest = AdapterManifest(
        name="qa-liar",
        version="0.1.0",
        api_version=ADAPTER_API_VERSION,
        standards=("qa",),
        capabilities=("export", "validate"),
        formats_out=("text/turtle",),
    )

    def export(self, ctx, request):
        return Artifact(content=b"qa", media_type="text/turtle")


def test_capability_without_method_is_invalid_not_ready(registry,
                                                        contract_context):
    problems = adapter_contract_problems(_ClaimsValidateWithoutMethod())
    assert problems
    assert any("validate" in problem for problem in problems)
    assert dict(CAPABILITY_METHODS)["import"] == "import_"
    assert adapter_contract_problems(registry.get("owl-rdf")) == []

    discovered = PluginRegistry(
        entry_points={"qa-liar": _ClaimsValidateWithoutMethod})
    assert discovered.discover() == ["owl-rdf", "skos"]
    assert discovered.names() == ["owl-rdf", "skos"]

    row = next(row for row in discovered.manifests()
               if row["name"] == "qa-liar")
    assert row["status"] == "invalid"
    assert any("validate" in problem for problem in row["problems"])
    with pytest.raises(AdapterError):
        discovered.get("qa-liar")

    # the broken neighbour does not affect the ready plugins
    for name in ("owl-rdf", "skos"):
        ready = next(row for row in discovered.manifests()
                     if row["name"] == name)
        assert ready["status"] == "ready"
        assert ready["problems"] == []
        assert discovered.get(name).export(contract_context,
                                           ExportRequest()).content

    with pytest.raises(AdapterError) as rejected:
        PluginRegistry().register(_ClaimsValidateWithoutMethod())
    assert "validate" in str(rejected.value)


# ---------------------------------------------------------------------------
# k) requires.ontoplatform compatibility (G-16, decision D3)
# ---------------------------------------------------------------------------

class _RequiresFuturisticPlatform:
    """A plugin that demands a platform build this one is not."""

    manifest = AdapterManifest(
        name="qa-future",
        version="1.0.0",
        api_version=ADAPTER_API_VERSION,
        standards=("qa",),
        capabilities=("export",),
        formats_out=("text/turtle",),
        requires={"ontoplatform": ">=99.0"},
    )

    def export(self, ctx, request):
        raise AssertionError("an incompatible adapter must never be invoked")


def test_incompatible_requires_is_listed_invalid_and_raises_on_get():
    registry = PluginRegistry(
        entry_points={"qa-future": _RequiresFuturisticPlatform})
    assert registry.discover() == ["owl-rdf", "skos"]
    assert registry.names() == ["owl-rdf", "skos"]
    assert registry.problems

    row = next(row for row in registry.manifests()
               if row["name"] == "qa-future")
    assert row["status"] == "invalid"
    assert any("incompatible" in problem for problem in row["problems"])
    assert row["requires"] == {"ontoplatform": ">=99.0", "extras": []}

    with pytest.raises(AdapterIncompatible):
        registry.get("qa-future")
    assert issubclass(AdapterIncompatible, AdapterError)

    compatible, reason = requires_compatible(
        _RequiresFuturisticPlatform.manifest)
    assert compatible is False
    assert "incompatible" in reason
    assert platform_version() in reason

    with pytest.raises(AdapterIncompatible):
        PluginRegistry().register(_RequiresFuturisticPlatform())


# ---------------------------------------------------------------------------
# l) explicit ABox / scope rejection (G-04)
# ---------------------------------------------------------------------------

def test_abox_scope_is_rejected_explicitly_not_silently_ignored(
        registry, contract_context):
    rejected_requests = (
        ExportRequest(scope="abox"),
        ExportRequest(scope="both"),
        ExportRequest(include_abox=True),
        ExportRequest(scope="abox", include_abox=True),
    )
    for name in ("owl-rdf", "skos"):
        adapter = registry.get(name)
        for request in rejected_requests:
            with pytest.raises(AdapterError) as error:
                adapter.export(contract_context, request)
            assert "TBox only" in str(error.value)

        for request in (ExportRequest(), ExportRequest(scope="tbox")):
            assert adapter.export(contract_context, request).content


def test_adapter_context_rejects_unknown_scope_names():
    with pytest.raises(AdapterError):
        build_context(_contract_ontology(), "qa-verifier", scope="dataset")


# ---------------------------------------------------------------------------
# m) language tags: CJK text gets @zh, ASCII identifiers stay xsd:string
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LANG_LITERAL = re.compile(
    r'"((?:[^"\\]|\\.)*)"@([A-Za-z]+(?:-[A-Za-z0-9]+)*)')


def test_language_tags_distinguish_cjk_text_from_ascii_identifiers(
        registry, contract_context):
    owl_text = registry.get("owl-rdf").export(
        contract_context, ExportRequest()).content.decode("utf-8")
    skos_text = registry.get("skos").export(
        contract_context, ExportRequest()).content.decode("utf-8")

    # OWL labels: the class name is ASCII, the description is Chinese
    assert 'rdfs:label "Vehicle"^^xsd:string' in owl_text
    assert 'rdfs:comment "车辆"@zh' in owl_text
    assert '"Vehicle"@zh' not in owl_text
    # SKOS labels: Chinese term vs. English alias
    assert 'skos:prefLabel "车辆"@zh' in skos_text
    assert 'skos:altLabel "车"@zh' in skos_text
    assert 'skos:altLabel "vehicle"^^xsd:string' in skos_text
    assert '"vehicle"@zh' not in skos_text

    # invariant: every language-tagged literal is @zh *and* contains CJK
    for text in (owl_text, skos_text):
        tagged = _LANG_LITERAL.findall(text)
        assert tagged
        for value, language in tagged:
            assert language == "zh", (value, language)
            assert _CJK.search(value), (value, language)


# ---------------------------------------------------------------------------
# n) plugin exception isolation: 502 + failure audit, never a 500 (G-03/G-21)
# ---------------------------------------------------------------------------

class _CrashingAdapter:
    """A plugin whose ``export`` explodes with a non-platform exception."""

    manifest = AdapterManifest(
        name="qa-crash",
        version="1.0.0",
        api_version=ADAPTER_API_VERSION,
        standards=("qa",),
        capabilities=("export",),
        formats_out=("text/turtle",),
    )

    def export(self, ctx, request):
        raise RuntimeError("qa plugin exploded")


class _RejectingAdapter:
    """A plugin that raises a platform error (a request-validation failure)."""

    manifest = AdapterManifest(
        name="qa-reject",
        version="1.0.0",
        api_version=ADAPTER_API_VERSION,
        standards=("qa",),
        capabilities=("export",),
        formats_out=("text/turtle",),
    )

    def export(self, ctx, request):
        raise AdapterError("qa request rejected by the plugin")


def _private_registry(*adapters) -> PluginRegistry:
    registry = PluginRegistry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


def test_adapter_crash_becomes_a_plugin_failure_with_failure_audit(
        platform, workspace_id):
    platform.standards = StandardsUseCases(
        platform.ctx, registry=_private_registry(
            _CrashingAdapter(), _RejectingAdapter()))

    with pytest.raises(AdapterInvocationError) as crashed:
        platform.standards.export(workspace_id, "admin", "qa-crash")
    details = classify(crashed.value)
    assert details["code"] == "plugin_failure"
    assert details["http_status"] == 502
    assert details["retryable"] is False
    assert "qa plugin exploded" in str(crashed.value)

    events = platform.ctx.audit.list(workspace_id, limit=50)
    failed = [event for event in events
              if event["action"] == "standards.export.failed"]
    assert len(failed) == 1
    event = failed[0]
    assert event["actor"] == "admin"
    assert event["target"] == "qa-crash"
    assert event["detail"]["error"] == "RuntimeError"
    assert "qa plugin exploded" in event["detail"]["message"]
    assert not any(event["action"] == "standards.export"
                   and event["target"] == "qa-crash" for event in events)

    # a platform *validation* error keeps its own taxonomy: only unexpected
    # exceptions are plugin failures (decision D7)
    with pytest.raises(AdapterError):
        platform.standards.export(workspace_id, "admin", "qa-reject")
    events = platform.ctx.audit.list(workspace_id, limit=50)
    assert not any(event["action"] == "standards.export.failed"
                   and event["target"] == "qa-reject" for event in events)


def test_http_adapter_crash_maps_to_502_plugin_failure(
        http_server, platform, workspace_id):
    base, wid = http_server
    platform.standards = StandardsUseCases(
        platform.ctx, registry=_private_registry(_CrashingAdapter()))

    status, headers, body = _raw_get(base, _export_path(wid, "qa-crash"))

    assert status == 502
    payload = json.loads(body)
    assert payload["type"] == "AdapterInvocationError"
    assert payload["code"] == "plugin_failure"
    assert payload["retryable"] is False
    assert "qa plugin exploded" in payload["error"]


def test_absent_standards_facade_is_a_404(http_server, platform, workspace_id):
    base, wid = http_server
    platform.standards = None

    status, headers, body = _raw_get(base, _export_path(wid, "owl-rdf"))
    assert status == 404
    payload = json.loads(body)
    assert payload["type"] == "NotFoundError"
    assert "not available" in payload["error"]

    status, headers, body = _raw_get(
        base, f"/api/workspaces/{wid}/standards/adapters")
    assert status == 404
    assert json.loads(body)["code"] == "not_found"


# ---------------------------------------------------------------------------
# o) provenance: response headers + deterministic use-case metadata (G-07)
# ---------------------------------------------------------------------------

def test_http_export_exposes_provenance_headers_and_charset(
        http_server, platform, workspace_id):
    base, wid = http_server
    published = published_ontology_row(platform.ctx, workspace_id)

    status, headers, body = _raw_get(
        base, _export_path(wid, "owl-rdf", "text/turtle"))
    assert status == 200
    assert headers["X-Adapter-Name"] == "owl-rdf"
    assert headers["X-Adapter-Version"] == "0.1.0"
    assert headers["X-Ontology-Version"] == "1"
    assert headers["X-Schema-Fingerprint"] == str(
        published["schema_fingerprint"])
    assert headers["Content-Type"].startswith("text/turtle")
    assert "charset=utf-8" in headers["Content-Type"].lower()
    assert headers["Content-Disposition"].endswith('.ttl"')

    # addressing the plugin by alias must not leak the alias into provenance
    alias_status, alias_headers, alias_body = _raw_get(
        base, _export_path(wid, "onto-standards-owl", "text/turtle"))
    assert alias_status == 200
    assert alias_headers["X-Adapter-Name"] == "owl-rdf"
    assert alias_body == body


def test_export_metadata_is_provenance_without_a_timestamp(platform,
                                                           workspace_id):
    published = published_ontology_row(platform.ctx, workspace_id)
    artifact = platform.standards.export(
        workspace_id, "admin", "onto-standards-skos")

    assert artifact["format"] == "text/turtle"
    assert artifact["content"]
    metadata = artifact["metadata"]
    assert metadata["workspace_id"] == workspace_id
    assert metadata["actor"] == "admin"
    assert metadata["ontology_version"] == int(published["version"])
    assert metadata["schema_fingerprint"] == str(
        published["schema_fingerprint"])
    assert metadata["adapter_version"] == "0.1.0"
    for timestamp_key in ("ts", "timestamp", "generated_at", "created_at"):
        assert timestamp_key not in metadata

    # determinism: the same published revision yields identical bytes/metadata
    again = platform.standards.export(workspace_id, "admin", "skos")
    assert again["content"] == artifact["content"]
    assert again["metadata"] == metadata


# ---------------------------------------------------------------------------
# p) frozen golden artifacts (G-18): rebuild the input from the artifact
# ---------------------------------------------------------------------------

GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "standards"

_PREFIX_LINE = re.compile(
    r"^@prefix\s+([A-Za-z][\w.-]*):\s*<([^>]+)>\s*\.\s*$")
_LITERAL = re.compile(
    r'^"((?:[^"\\]|\\.)*)"'
    r'(?:\^\^(\S+)|@([A-Za-z]+(?:-[A-Za-z0-9]+)*))?$')
_TURTLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t",
                   '"': '"', "\\": "\\", "'": "'"}

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
_RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"
_RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain"
_RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range"
_OWL_NAMESPACE = "http://www.w3.org/2002/07/owl#"
_OWL_CLASS = _OWL_NAMESPACE + "Class"
_OWL_DATATYPE_PROPERTY = _OWL_NAMESPACE + "DatatypeProperty"
_OWL_OBJECT_PROPERTY = _OWL_NAMESPACE + "ObjectProperty"
_OWL_VERSION_INFO = _OWL_NAMESPACE + "versionInfo"
_OWL_INVERSE_OF = _OWL_NAMESPACE + "inverseOf"
_SKOS_NAMESPACE = "http://www.w3.org/2004/02/skos/core#"
_SKOS_CONCEPT = _SKOS_NAMESPACE + "Concept"
_SKOS_PREF_LABEL = _SKOS_NAMESPACE + "prefLabel"
_SKOS_ALT_LABEL = _SKOS_NAMESPACE + "altLabel"
_SKOS_NOTATION = _SKOS_NAMESPACE + "notation"
_XSD = "http://www.w3.org/2001/XMLSchema#"
_XSD_RANGE_TO_TYPE = {
    _XSD + "string": "string",
    _XSD + "integer": "integer",
    _XSD + "decimal": "number",
    _XSD + "boolean": "boolean",
    _XSD + "dateTime": "datetime",
}


def _turtle_lines(path: pathlib.Path):
    prefixes: dict[str, str] = {}
    triples: list[tuple[str, str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        declared = _PREFIX_LINE.match(line)
        if declared:
            prefixes[declared.group(1)] = declared.group(2)
            continue
        if line.endswith(" ."):
            line = line[:-2].rstrip()
        parts = line.split(" ", 2)
        if len(parts) == 3:
            triples.append(tuple(parts))
    return prefixes, triples


def _expand_term(token: str, prefixes: dict[str, str]) -> str:
    token = token.strip()
    if token.startswith("<") and token.endswith(">"):
        return token[1:-1]
    prefix, separator, local = token.partition(":")
    if not separator or prefix not in prefixes:
        return token
    return prefixes[prefix] + local


def _unescape_turtle(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            escape = text[index + 1]
            if escape in _TURTLE_ESCAPES:
                out.append(_TURTLE_ESCAPES[escape])
                index += 2
                continue
            if escape == "u":
                out.append(chr(int(text[index + 2:index + 6], 16)))
                index += 6
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _literal(token: str):
    match = _LITERAL.match(token.strip())
    if match is None:
        return "", None
    return _unescape_turtle(match.group(1)), match.group(3)


def _golden_ontology() -> Ontology:
    """Reconstruct the export input from the frozen OWL/SKOS artifacts.

    The golden files are the contract, so the aggregate is derived from them
    (not from the developer's fixture): a drift in either direction fails.
    """
    prefixes, triples = _turtle_lines(GOLDEN_DIR / "owl-rdf-v2.ttl")
    base = prefixes["onto"]
    ontology_iri = base[:-1]
    workspace = re.search(r"/ws/([^/]+)/onto/", base).group(1)
    version = int(re.search(r"/v(\d+)#$", base).group(1))

    name = fingerprint = ""
    for subject, predicate, object_ in triples:
        if _expand_term(subject, prefixes) != ontology_iri:
            continue
        predicate_iri = _expand_term(predicate, prefixes)
        if predicate_iri == _RDFS_LABEL:
            name, _ = _literal(object_)
        elif predicate_iri == _OWL_VERSION_INFO:
            value, _ = _literal(object_)
            fingerprint = value.partition("fingerprint:")[2]
    assert name and fingerprint, "the golden artifact header is not readable"

    subjects: dict[str, dict] = {}
    for subject, predicate, object_ in triples:
        if _expand_term(predicate, prefixes) != _RDF_TYPE:
            continue
        kind = _expand_term(object_, prefixes)
        if kind in (_OWL_CLASS, _OWL_DATATYPE_PROPERTY, _OWL_OBJECT_PROPERTY):
            subjects[_expand_term(subject, prefixes)] = {"kind": kind}
    for subject, predicate, object_ in triples:
        subject_iri = _expand_term(subject, prefixes)
        if subject_iri not in subjects:
            continue
        predicate_iri = _expand_term(predicate, prefixes)
        if predicate_iri == _RDFS_COMMENT:
            subjects[subject_iri]["comment"], _ = _literal(object_)
        elif predicate_iri == _RDFS_DOMAIN:
            subjects[subject_iri]["domain"] = _expand_term(object_, prefixes)
        elif predicate_iri == _RDFS_RANGE:
            subjects[subject_iri]["range"] = _expand_term(object_, prefixes)
        elif predicate_iri == _OWL_INVERSE_OF:
            subjects[subject_iri]["inverse"] = _expand_term(object_, prefixes)

    def _local(iri: str) -> str:
        return iri[len(base):] if iri.startswith(base) else iri

    data: dict = {
        "id": "onto-qa-golden",
        "workspace_id": workspace,
        "name": name,
        "version": version,
        "schema_version": 1,
        "schema_fingerprint": fingerprint,
        "profile": "closed",
        "objects": {},
        "relations": {},
        "vocabulary": {},
    }
    for iri, info in subjects.items():
        local = _local(iri)
        if info["kind"] == _OWL_CLASS:
            data["objects"][local] = {
                "name": local,
                "description": info.get("comment", ""),
                "identity": [],
                "category": "object",
                "properties": {},
            }
        elif info["kind"] == _OWL_DATATYPE_PROPERTY:
            owner, _, prop = local.partition(".")
            data["objects"].setdefault(owner, {
                "name": owner,
                "description": "",
                "identity": [],
                "category": "object",
                "properties": {},
            })["properties"][prop] = {
                "name": prop,
                "type": _XSD_RANGE_TO_TYPE[info["range"]],
                "description": info.get("comment", ""),
            }
        else:
            data["relations"][local] = {
                "name": local,
                "source": _local(info.get("domain", "")),
                "target": _local(info.get("range", "")),
                "description": "",
                "inverse": _local(info.get("inverse", "")),
            }

    skos_prefixes, skos_triples = _turtle_lines(GOLDEN_DIR / "skos-v2.ttl")
    concepts: dict[str, dict] = {}
    for subject, predicate, object_ in skos_triples:
        if (_expand_term(predicate, skos_prefixes) == _RDF_TYPE
                and _expand_term(object_, skos_prefixes) == _SKOS_CONCEPT):
            concepts[_expand_term(subject, skos_prefixes)] = {
                "term": "", "concept": "", "kind": "object", "aliases": []}
    for subject, predicate, object_ in skos_triples:
        iri = _expand_term(subject, skos_prefixes)
        if iri not in concepts:
            continue
        predicate_iri = _expand_term(predicate, skos_prefixes)
        if predicate_iri == _SKOS_PREF_LABEL:
            concepts[iri]["term"], _ = _literal(object_)
        elif predicate_iri == _SKOS_NOTATION:
            concepts[iri]["concept"], _ = _literal(object_)
        elif predicate_iri == _SKOS_ALT_LABEL:
            concepts[iri]["aliases"].append(_literal(object_)[0])
    for concept in concepts.values():
        data["vocabulary"][concept["term"]] = concept

    return Ontology.from_dict(data)


def test_owl_golden_is_byte_identical_and_structurally_complete(registry):
    artifact = registry.get("owl-rdf").export(
        build_context(_golden_ontology(), "qa-verifier"), ExportRequest())
    golden = (GOLDEN_DIR / "owl-rdf-v2.ttl").read_bytes()

    assert artifact.content == golden
    assert artifact.filename == "traffic-v2.ttl"

    text = golden.decode("utf-8")
    assert text.count("rdf:type owl:Class .") == 2
    assert text.count("rdf:type owl:DatatypeProperty .") == 4
    assert text.count("rdf:type owl:ObjectProperty .") == 2
    assert text.count("owl:inverseOf") == 2
    assert '"车辆"@zh' in text
    assert '"Vehicle"^^xsd:string' in text
    for marker in SEMANTIC_RED_LINE:
        assert marker not in text


def test_skos_golden_is_byte_identical_and_structurally_complete(registry):
    artifact = registry.get("skos").export(
        build_context(_golden_ontology(), "qa-verifier"), ExportRequest())
    golden = (GOLDEN_DIR / "skos-v2.ttl").read_bytes()

    assert artifact.content == golden
    assert artifact.filename == "traffic-v2.skos.ttl"

    text = golden.decode("utf-8")
    assert text.count("rdf:type skos:ConceptScheme .") == 1
    assert text.count("rdf:type skos:Concept .") == 3
    assert text.count("skos:altLabel") == 3
    assert text.count("skos:prefLabel") == 4
    assert '"收费站"@zh' in text
    assert '"toll station"^^xsd:string' in text


# ---------------------------------------------------------------------------
# q) cross-process determinism under different PYTHONHASHSEED values (G-20)
# ---------------------------------------------------------------------------

_DETERMINISM_SCRIPT = '''\
import json
import sys

sys.path.insert(0, __SRC_DIR__)

from onto_platform.domain.ontology.model import Ontology
from onto_platform.semantic import ExportRequest, build_context
from onto_platform.semantic.owl import OwlRdfAdapter
from onto_platform.semantic.skos import SkosAdapter

ontology = Ontology.from_dict(json.loads(__PAYLOAD__))
context = build_context(ontology, "qa-cross-process")
parts = [
    OwlRdfAdapter().export(context, ExportRequest()).content,
    SkosAdapter().export(context, ExportRequest()).content,
]
sys.stdout.buffer.write(b"\\x00".join(parts))
sys.stdout.buffer.flush()
'''


def _run_export_process(seed: str) -> subprocess.CompletedProcess:
    payload = json.dumps(_golden_ontology().to_dict())
    script = (_DETERMINISM_SCRIPT
              .replace("__SRC_DIR__", repr(str(REPO_ROOT / "src")))
              .replace("__PAYLOAD__", repr(payload)))
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    source_path = str(REPO_ROOT / "src")
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        source_path + (os.pathsep + existing if existing else ""))

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", "replace")
    return completed


def test_export_is_byte_identical_across_processes_with_different_hash_seeds(
        registry):
    first = _run_export_process("1")
    second = _run_export_process("2")

    assert first.stdout
    assert first.stdout == second.stdout

    context = build_context(_golden_ontology(), "qa-verifier")
    expected = b"\x00".join([
        registry.get("owl-rdf").export(context, ExportRequest()).content,
        registry.get("skos").export(context, ExportRequest()).content,
    ])
    assert first.stdout == expected


# ---------------------------------------------------------------------------
# r) package surface: the completion helpers are public
# ---------------------------------------------------------------------------

def test_semantic_package_exposes_the_completion_contract():
    import onto_platform.semantic as semantic_package

    for exported in ("AdapterInvocationError", "CAPABILITY_METHODS",
                     "adapter_contract_problems", "platform_version",
                     "requires_compatible", "requires_problems"):
        assert hasattr(semantic_package, exported), exported
