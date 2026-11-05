"""OWL/RDF and SKOS export adapters: mapping, semantic red lines, determinism.

Covers the T2 acceptance criteria (docs/standards-plugin-implementation-plan-v1.md
§4 T2) and the OWL/SHACL division of labour from
docs/standards-plugin-architecture.md §2.5.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from onto_platform.domain.ontology.model import ObjectTypeSpec, Ontology
from onto_platform.semantic.iri import IriPolicy
from onto_platform.semantic.owl import OwlRdfAdapter
from onto_platform.semantic.skos import SkosAdapter
from onto_platform.semantic.spi import (
    AdapterError,
    ExportRequest,
    manifest_problems,
)

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"

#: Platform runtime constraints must never surface as OWL axioms (§2.5).
SEMANTIC_RED_LINES = (
    "owl:hasKey",
    "owl:cardinality",
    "owl:minCardinality",
    "owl:maxCardinality",
    "owl:FunctionalProperty",
    "owl:someValuesFrom",
    "owl:allValuesFrom",
)

PLATE_DESCRIPTION = '车牌号码，例如 "鲁A12345"\n第二行\\反斜杠'


def build_ontology(version: int = 2) -> Ontology:
    """Sample traffic ontology: 2 object types, 4 properties, 2 relations, 2 terms."""
    return Ontology.from_dict({
        "id": "onto-traffic",
        "workspace_id": "ws-1",
        "name": "traffic",
        "version": version,
        "schema_version": 1,
        "schema_fingerprint": "abc123",
        "profile": "closed",
        "objects": {
            "Vehicle": {
                "name": "Vehicle",
                "description": "车辆",
                "identity": ["plate"],
                "category": "object",
                "properties": {
                    "plate": {
                        "name": "plate",
                        "type": "string",
                        "required": True,
                        "description": PLATE_DESCRIPTION,
                    },
                    "balance": {"name": "balance", "type": "number"},
                    "since": {"name": "since", "type": "datetime"},
                    "active": {"name": "active", "type": "boolean"},
                },
            },
            "TollStation": {
                "name": "TollStation",
                "description": "收费站",
                "category": "object",
                "properties": {},
            },
        },
        "relations": {
            "passes": {
                "name": "passes",
                "source": "Vehicle",
                "target": "TollStation",
                "description": "车辆通过收费站",
                "cardinality": "many-to-one",
                "inverse": "passed_by",
            },
            "passed_by": {
                "name": "passed_by",
                "source": "TollStation",
                "target": "Vehicle",
                "inverse": "passes",
            },
        },
        "vocabulary": {
            "收费站": {
                "term": "收费站",
                "concept": "TollStation",
                "kind": "object",
                "aliases": ["收费站点", "toll station"],
            },
            "车辆": {
                "term": "车辆",
                "concept": "Vehicle",
                "kind": "object",
                "aliases": [],
            },
        },
    })


def build_ctx(ontology: Ontology) -> SimpleNamespace:
    """AdapterContext duck type: a raw Ontology is a valid ``ctx.tbox``."""
    return SimpleNamespace(
        tbox=ontology,
        iri=IriPolicy.for_ontology(ontology),
        version="1",
        workspace_id="ws-1",
        actor="tester",
        scope="tbox",
        abox=None,
    )


def export_text(adapter, ctx, format: str = "text/turtle") -> str:
    return adapter.export(ctx, ExportRequest(format=format)).content.decode("utf-8")


def qname(ctx: SimpleNamespace, iri: str) -> str:
    """Render a term IRI the way the writer abbreviates it."""
    base = ctx.iri.base
    assert iri.startswith(base), iri
    return "onto:" + iri[len(base):]


# ---------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------


def test_both_manifests_are_contract_valid():
    owl = OwlRdfAdapter()
    skos = SkosAdapter()
    assert manifest_problems(owl.manifest) == []
    assert manifest_problems(skos.manifest) == []

    assert owl.manifest.name == "owl-rdf"
    assert owl.manifest.api_version == "1.0"
    assert owl.manifest.capabilities == ("export",)
    assert owl.manifest.formats_out == ("text/turtle",)
    assert set(owl.manifest.standards) >= {"owl", "rdf", "turtle"}
    assert owl.manifest.permissions == {"network": False, "write": False}

    assert skos.manifest.name == "skos"
    assert skos.manifest.api_version == "1.0"
    assert skos.manifest.capabilities == ("export",)
    assert skos.manifest.formats_out == ("text/turtle",)
    assert skos.manifest.standards == ("skos",)


# ---------------------------------------------------------------------------
# OWL/RDF export
# ---------------------------------------------------------------------------


def test_owl_document_header():
    ctx = build_ctx(build_ontology())
    text = export_text(OwlRdfAdapter(), ctx)
    iri = ctx.iri

    assert f"<{iri.ontology_iri()}> rdf:type owl:Ontology ." in text
    assert (f'<{iri.ontology_iri()}> rdfs:label "traffic"^^xsd:string .'
            in text)
    assert ("owl:versionInfo \"v2 fingerprint:abc123\"^^xsd:string ."
            in text)
    assert (f"<{iri.ontology_iri()}> owl:versionIRI <{iri.version_iri()}> ."
            in text)
    assert "@prefix onto:" in text


def test_owl_classes_labels_and_comments():
    ctx = build_ctx(build_ontology())
    text = export_text(OwlRdfAdapter(), ctx)
    vehicle = qname(ctx, ctx.iri.class_iri("Vehicle"))
    station = qname(ctx, ctx.iri.class_iri("TollStation"))

    for class_name, label in ((vehicle, "Vehicle"), (station, "TollStation")):
        assert f"{class_name} rdf:type owl:Class ." in text
        assert f'{class_name} rdfs:label "{label}"^^xsd:string .' in text
    assert f'{vehicle} rdfs:comment "车辆"^^xsd:string .' in text
    assert f'{station} rdfs:comment "收费站"^^xsd:string .' in text


def test_owl_relations_domain_range_and_inverse():
    ctx = build_ctx(build_ontology())
    text = export_text(OwlRdfAdapter(), ctx)
    passes = qname(ctx, ctx.iri.relation_iri("passes"))
    passed_by = qname(ctx, ctx.iri.relation_iri("passed_by"))
    vehicle = qname(ctx, ctx.iri.class_iri("Vehicle"))
    station = qname(ctx, ctx.iri.class_iri("TollStation"))

    assert f"{passes} rdf:type owl:ObjectProperty ." in text
    assert f"{passes} rdfs:domain {vehicle} ." in text
    assert f"{passes} rdfs:range {station} ." in text
    assert f"{passes} owl:inverseOf {passed_by} ." in text
    assert f"{passed_by} owl:inverseOf {passes} ." in text


def test_owl_datatype_properties_and_xsd_ranges():
    ctx = build_ctx(build_ontology())
    text = export_text(OwlRdfAdapter(), ctx)
    vehicle = qname(ctx, ctx.iri.class_iri("Vehicle"))

    expected_ranges = {
        "plate": "xsd:string",
        "balance": "xsd:decimal",
        "since": "xsd:dateTime",
        "active": "xsd:boolean",
    }
    for name, range_ in expected_ranges.items():
        prop = qname(ctx, ctx.iri.property_iri("Vehicle", name))
        assert f"{prop} rdf:type owl:DatatypeProperty ." in text
        assert f"{prop} rdfs:domain {vehicle} ." in text
        assert f"{prop} rdfs:range {range_} ." in text

    plate = qname(ctx, ctx.iri.property_iri("Vehicle", "plate"))
    assert (f'{plate} rdfs:comment '
            '"车牌号码，例如 \\"鲁A12345\\"\\n第二行\\\\反斜杠"^^xsd:string .'
            in text)


def test_owl_does_not_emit_runtime_constraints():
    ctx = build_ctx(build_ontology())
    text = export_text(OwlRdfAdapter(), ctx)

    for marker in SEMANTIC_RED_LINES:
        assert marker not in text, f"OWL export must not contain {marker}"
    assert "sh:" not in text
    # required/unique/enum facets are SHACL territory (P1), not OWL
    assert "required" not in text
    assert "owl:Restriction" not in text
    assert "owl:onProperty" not in text


def test_owl_skips_undeclared_inverse_and_reports_it():
    ontology = build_ontology()
    ontology.relations["passes"].inverse = "ghost_inverse"
    ctx = build_ctx(ontology)
    artifact = OwlRdfAdapter().export(ctx)
    text = artifact.content.decode("utf-8")
    passes = qname(ctx, ctx.iri.relation_iri("passes"))

    assert f"{passes} owl:inverseOf" not in text
    assert artifact.metadata["skipped_inverse"] == ["passes->ghost_inverse"]
    # the still-declared inverse direction is unaffected
    assert "owl:inverseOf" in text


def test_owl_fails_fast_on_iri_collision():
    ontology = build_ontology()
    # A class named like the relation would map to the same fragment.
    ontology.objects["passes"] = ObjectTypeSpec(name="passes")
    ctx = build_ctx(ontology)

    with pytest.raises(AdapterError):
        OwlRdfAdapter().export(ctx)


def test_owl_artifact_and_metadata():
    ctx = build_ctx(build_ontology())
    artifact = OwlRdfAdapter().export(ctx, ExportRequest(format="text/turtle"))

    assert isinstance(artifact.content, bytes)
    assert artifact.content
    assert artifact.media_type == "text/turtle"
    assert artifact.filename == "traffic-v2.ttl"
    assert artifact.metadata["adapter"] == "owl-rdf"
    assert artifact.metadata["version"] == OwlRdfAdapter.manifest.version
    assert artifact.metadata["schema_fingerprint"] == "abc123"
    assert artifact.metadata["terms"] == 2 + 4 + 2  # classes + properties + relations


def test_owl_is_deterministic_across_exports():
    first = OwlRdfAdapter().export(build_ctx(build_ontology()))
    second = OwlRdfAdapter().export(build_ctx(build_ontology()))
    assert first.content == second.content


def test_owl_version_bump_changes_namespace_and_filename():
    ctx = build_ctx(build_ontology(version=3))
    artifact = OwlRdfAdapter().export(ctx)
    text = artifact.content.decode("utf-8")
    assert artifact.filename == "traffic-v3.ttl"
    assert "https://ontoplatform.local/ws/ws-1/onto/traffic/v3#" in text
    assert "v3 fingerprint:abc123" in text


def test_owl_accepts_documented_formats():
    ctx = build_ctx(build_ontology())
    adapter = OwlRdfAdapter()
    contents = {
        adapter.export(ctx, ExportRequest(format=fmt)).content
        for fmt in ("", "text/turtle", "turtle")
    }
    assert len(contents) == 1


@pytest.mark.parametrize("format_name", [
    "application/rdf+xml", "json-ld", "text/shacl", "n-triples",
])
def test_owl_rejects_unsupported_formats(format_name):
    with pytest.raises(AdapterError):
        OwlRdfAdapter().export(
            build_ctx(build_ontology()), ExportRequest(format=format_name))


def test_owl_rejects_context_without_tbox_or_iri():
    request = ExportRequest()
    with pytest.raises(AdapterError):
        OwlRdfAdapter().export(SimpleNamespace(iri=None), request)
    with pytest.raises(AdapterError):
        OwlRdfAdapter().export(SimpleNamespace(tbox=None, iri=None), request)


# ---------------------------------------------------------------------------
# SKOS export
# ---------------------------------------------------------------------------


def test_skos_concept_scheme_and_concepts():
    ctx = build_ctx(build_ontology())
    text = export_text(SkosAdapter(), ctx)
    scheme = qname(ctx, ctx.iri.concept_scheme_iri())

    assert f"{scheme} rdf:type skos:ConceptScheme ." in text
    assert f'{scheme} skos:prefLabel "traffic"^^xsd:string .' in text
    concept_lines = [
        line for line in text.splitlines()
        if line.endswith("rdf:type skos:Concept .")
    ]
    assert len(concept_lines) == 2


def test_skos_term_labels_notation_and_scheme_link():
    ctx = build_ctx(build_ontology())
    text = export_text(SkosAdapter(), ctx)
    scheme = qname(ctx, ctx.iri.concept_scheme_iri())
    station = qname(ctx, ctx.iri.vocabulary_iri("收费站"))

    assert f'{station} skos:prefLabel "收费站"^^xsd:string .' in text
    assert f'{station} skos:notation "TollStation"^^xsd:string .' in text
    assert f'{station} skos:altLabel "toll station"^^xsd:string .' in text
    assert f'{station} skos:altLabel "收费站点"^^xsd:string .' in text
    assert f"{station} skos:inScheme {scheme} ." in text
    # the term without aliases still becomes a concept
    vehicle = qname(ctx, ctx.iri.vocabulary_iri("车辆"))
    assert f'{vehicle} skos:prefLabel "车辆"^^xsd:string .' in text


def test_skos_definition_when_term_exposes_description():
    tbox = SimpleNamespace(
        workspace_id="ws-1",
        name="traffic",
        version=2,
        schema_fingerprint="abc123",
        objects={},
        relations={},
        vocabulary={
            "收费站": SimpleNamespace(
                term="收费站",
                concept="TollStation",
                aliases=["收费站点"],
                description="车辆经过的收费站",
            ),
        },
    )
    ctx = SimpleNamespace(tbox=tbox, iri=IriPolicy.for_ontology(tbox))
    text = export_text(SkosAdapter(), ctx)
    station = qname(ctx, ctx.iri.vocabulary_iri("收费站"))
    assert (f'{station} skos:definition "车辆经过的收费站"^^xsd:string .'
            in text)


def test_skos_artifact_and_metadata():
    ctx = build_ctx(build_ontology())
    artifact = SkosAdapter().export(ctx)
    assert artifact.media_type == "text/turtle"
    assert artifact.filename == "traffic-v2.skos.ttl"
    assert artifact.metadata["adapter"] == "skos"
    assert artifact.metadata["version"] == SkosAdapter.manifest.version
    assert artifact.metadata["schema_fingerprint"] == "abc123"
    assert artifact.metadata["concepts"] == 2


def test_skos_is_deterministic_across_exports():
    first = SkosAdapter().export(build_ctx(build_ontology()))
    second = SkosAdapter().export(build_ctx(build_ontology()))
    assert first.content == second.content
    assert isinstance(first.content, bytes) and first.content


def test_skos_accepts_documented_formats():
    ctx = build_ctx(build_ontology())
    adapter = SkosAdapter()
    contents = {
        adapter.export(ctx, ExportRequest(format=fmt)).content
        for fmt in ("", "text/turtle", "turtle")
    }
    assert len(contents) == 1


@pytest.mark.parametrize("format_name", ["application/ld+json", "application/rdf+xml"])
def test_skos_rejects_unsupported_formats(format_name):
    with pytest.raises(AdapterError):
        SkosAdapter().export(
            build_ctx(build_ontology()), ExportRequest(format=format_name))


# ---------------------------------------------------------------------------
# optional interop: parse products with rdflib when the extra is installed
# ---------------------------------------------------------------------------


def _import_rdflib_or_skip():
    if importlib.util.find_spec("rdflib") is None:
        pytest.skip(
            "rdflib is not installed (the core stays dependency-free); "
            "the [interop] extra enables this check")
    import rdflib  # noqa: PLC0415 - optional dependency

    return rdflib


def test_owl_artifact_parses_with_rdflib():
    rdflib = _import_rdflib_or_skip()
    ctx = build_ctx(build_ontology())
    artifact = OwlRdfAdapter().export(ctx)
    graph = rdflib.Graph()
    graph.parse(data=artifact.content.decode("utf-8"), format="turtle")

    rdf = rdflib.Namespace(RDF)
    owl = rdflib.Namespace(OWL)
    onto = rdflib.Namespace(ctx.iri.base)
    assert len(graph) > 0
    assert (onto.Vehicle, rdf.type, owl.Class) in graph
    assert (onto.passes, rdf.type, owl.ObjectProperty) in graph
    assert (onto.passes, owl.inverseOf, onto.passed_by) in graph
    assert (onto["Vehicle.plate"], rdflib.URIRef(RDFS + "range"),
            rdflib.URIRef(XSD + "string")) in graph


def test_skos_artifact_parses_with_rdflib():
    rdflib = _import_rdflib_or_skip()
    ctx = build_ctx(build_ontology())
    artifact = SkosAdapter().export(ctx)
    graph = rdflib.Graph()
    graph.parse(data=artifact.content.decode("utf-8"), format="turtle")

    rdf = rdflib.Namespace(RDF)
    skos = rdflib.Namespace(SKOS)
    scheme = rdflib.URIRef(ctx.iri.concept_scheme_iri())
    assert len(graph) > 0
    assert (scheme, rdf.type, skos.ConceptScheme) in graph
    concepts = list(graph.subjects(rdf.type, skos.Concept))
    assert len(concepts) == 2
