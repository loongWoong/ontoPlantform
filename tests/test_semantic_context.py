"""Read-only adapter context tests (T1, standards plugin P0).

The frozen ``AdapterContext`` is the plugin's only read surface: a
snapshotted TBox view, the deterministic IRI policy, the actor and scope.
``OntologyView.compiled_schema()`` must agree with a direct
``OntologyCompiler().compile(ontology, [])`` and must never hand out a
shared mutable schema.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from onto_platform.domain.ontology.compiler import OntologyCompiler
from onto_platform.domain.ontology.model import Ontology
from onto_platform.semantic.context import (
    AdapterContext,
    OntologyView,
    SnapshotView,
    build_context,
)
from onto_platform.semantic.iri import IriPolicy, iri_policy_for
from onto_platform.semantic.spi import AdapterError


def _ontology(**overrides) -> Ontology:
    data = {
        "id": "ont-1",
        "workspace_id": "ws-1",
        "name": "traffic",
        "description": "高速运营本体",
        "version": 3,
        "schema_version": 3,
        "schema_fingerprint": "sha256:abc",
        "profile": "closed",
        "objects": {
            "Vehicle": {
                "name": "Vehicle",
                "properties": {
                    "plate": {"name": "plate", "type": "string", "required": True},
                    "balance": {
                        "name": "balance", "type": "number",
                        "minimum": 0, "unit": "CNY",
                    },
                },
                "identity": ["plate"],
            },
            "TollStation": {
                "name": "TollStation",
                "properties": {"name": {"name": "name", "type": "string"}},
            },
        },
        "relations": {
            "passes": {
                "name": "passes",
                "source": "Vehicle",
                "target": "TollStation",
                "cardinality": "many-to-one",
                "inverse": "passed_by",
            },
        },
        "vocabulary": {
            "车辆": {"term": "车辆", "concept": "Vehicle", "kind": "object"},
        },
    }
    data.update(overrides)
    return Ontology.from_dict(data)


# ----------------------------------------------------------------------
# OntologyView
# ----------------------------------------------------------------------


def test_view_mirrors_ontology_fields():
    ontology = _ontology()
    view = OntologyView.from_ontology(ontology)

    assert view.id == "ont-1"
    assert view.workspace_id == "ws-1"
    assert view.name == "traffic"
    assert view.version == 3
    assert view.schema_version == 3
    assert view.schema_fingerprint == "sha256:abc"
    assert view.profile == "closed"
    assert set(view.objects) == {"Vehicle", "TollStation"}
    assert set(view.relations) == {"passes"}
    assert set(view.vocabulary) == {"车辆"}
    assert view.objects["Vehicle"].identity == ["plate"]


def test_view_snapshots_the_source_ontology():
    ontology = _ontology()
    view = OntologyView.from_ontology(ontology)
    expected_schema = view.compiled_schema()

    ontology.name = "mutated"
    ontology.objects["Vehicle"].description = "mutated"

    assert view.name == "traffic"
    assert view.objects["Vehicle"].description == ""
    assert view.compiled_schema() == expected_schema


def test_compiled_schema_matches_the_ontology_compiler():
    ontology = _ontology()
    view = OntologyView.from_ontology(ontology)

    expected = OntologyCompiler().compile(ontology, []).schema
    assert view.compiled_schema() == expected


def test_compiled_schema_is_rebuilt_on_every_call():
    view = OntologyView.from_ontology(_ontology())

    first = view.compiled_schema()
    first["objects"]["Vehicle"]["description"] = "tampered"

    second = view.compiled_schema()
    assert second is not first
    assert second["objects"]["Vehicle"]["description"] == ""


def test_view_is_frozen_and_mappings_are_read_only():
    view = OntologyView.from_ontology(_ontology())

    with pytest.raises(FrozenInstanceError):
        view.name = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        view.objects["Ghost"] = view.objects["Vehicle"]  # type: ignore[index]
    with pytest.raises(TypeError):
        del view.relations["passes"]  # type: ignore[attr-defined]


def test_compiled_schema_requires_an_embedded_ontology():
    view = OntologyView(
        id="ont-1",
        workspace_id="ws-1",
        name="traffic",
        version=1,
        schema_version=1,
        schema_fingerprint="",
        profile="closed",
    )

    with pytest.raises(AdapterError):
        view.compiled_schema()


# ----------------------------------------------------------------------
# SnapshotView (P0 placeholder)
# ----------------------------------------------------------------------


def test_snapshot_view_is_an_empty_frozen_placeholder():
    snapshot = SnapshotView()

    assert snapshot.objects == ()
    with pytest.raises(FrozenInstanceError):
        snapshot.objects = ({"id": "1"},)  # type: ignore[misc]


# ----------------------------------------------------------------------
# AdapterContext / build_context
# ----------------------------------------------------------------------


def test_build_context_from_ontology():
    ontology = _ontology()
    context = build_context(ontology, "alice")

    assert isinstance(context, AdapterContext)
    assert context.workspace_id == "ws-1"
    assert context.actor == "alice"
    assert context.scope == "tbox"
    assert context.abox is None
    assert context.version == "3"
    assert context.tbox.name == "traffic"
    assert context.iri == IriPolicy.for_ontology(ontology)
    assert context.iri.class_iri("Vehicle").startswith(
        "https://ontoplatform.local/ws/ws-1/onto/traffic/v3#")


def test_build_context_supports_scope_and_custom_base():
    ontology = _ontology()
    context = build_context(ontology, "alice", scope="both",
                            base="https://data.example.org")

    assert context.scope == "both"
    assert context.iri == iri_policy_for(ontology, "https://data.example.org")
    assert context.iri.base == "https://data.example.org/ws/ws-1/onto/traffic/v3#"


def test_build_context_tolerates_a_trailing_slash_in_base():
    context = build_context(_ontology(), "alice", base="https://data.example.org/")

    assert context.iri.base == "https://data.example.org/ws/ws-1/onto/traffic/v3#"


def test_build_context_rejects_unknown_scope():
    with pytest.raises(AdapterError) as excinfo:
        build_context(_ontology(), "alice", scope="everything")
    assert "scope" in str(excinfo.value)


def test_adapter_context_is_frozen():
    context = build_context(_ontology(), "alice")

    with pytest.raises(FrozenInstanceError):
        context.actor = "bob"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.scope = "abox"  # type: ignore[misc]
