"""Deterministic IRI policy tests (T1, standards plugin P0).

Covers the frozen ``IriPolicy`` contract from the implementation plan §2.2:

* IRIs are deterministic for the same ontology version;
* a version bump changes only the namespace version segment, never the
  local fragment;
* class/relation/property/vocabulary collisions are detected before an
  adapter writes a document;
* empty workspace/ontology names are rejected.
"""

from __future__ import annotations

import pytest

from onto_platform.domain.ontology.model import Ontology, RelationSpec
from onto_platform.semantic.iri import (
    DEFAULT_BASE,
    PREFIXES,
    IriPolicy,
    iri_policy_for,
)
from onto_platform.semantic.spi import AdapterError


def _ontology_data(**overrides) -> dict:
    data = {
        "id": "ont-1",
        "workspace_id": "ws-1",
        "name": "traffic",
        "version": 1,
        "schema_version": 1,
        "profile": "closed",
        "objects": {
            "Vehicle": {
                "name": "Vehicle",
                "description": "车辆",
                "properties": {
                    "plate": {"name": "plate", "type": "string", "required": True},
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
            },
        },
        "vocabulary": {
            "车辆": {"term": "车辆", "concept": "Vehicle", "kind": "object"},
        },
    }
    data.update(overrides)
    return data


def _ontology(**overrides) -> Ontology:
    return Ontology.from_dict(_ontology_data(**overrides))


def _policy(**overrides) -> IriPolicy:
    return IriPolicy.for_ontology(_ontology(**overrides))


# ----------------------------------------------------------------------
# determinism and shape
# ----------------------------------------------------------------------


def test_iris_are_deterministic_for_the_same_version():
    first = _policy()
    second = _policy()

    assert first == second
    assert first.base == second.base
    assert first.class_iri("Vehicle") == second.class_iri("Vehicle")
    assert first.relation_iri("passes") == second.relation_iri("passes")


def test_iri_shape_is_workspace_ontology_version_and_fragment():
    policy = _policy()

    assert policy.base == "https://ontoplatform.local/ws/ws-1/onto/traffic/v1#"
    assert policy.ontology_iri() == "https://ontoplatform.local/ws/ws-1/onto/traffic/v1"
    assert policy.version_iri() == policy.ontology_iri() + "/version/1"
    assert policy.class_iri("Vehicle") == policy.base + "Vehicle"
    assert policy.relation_iri("passes") == policy.base + "passes"
    assert policy.property_iri("Vehicle", "plate") == policy.base + "Vehicle.plate"
    assert policy.concept_scheme_iri() == policy.base + "ConceptScheme"


def test_vocabulary_terms_are_percent_encoded_deterministically():
    policy = _policy()

    iri = policy.vocabulary_iri("车辆")
    assert iri == policy.base + "term.%E8%BD%A6%E8%BE%86"
    assert policy.vocabulary_iri("车辆") == iri


def test_prefix_map_declares_w3c_prefixes_and_onto_namespace():
    policy = _policy()
    prefixes = policy.prefix_map()

    assert prefixes["onto"] == policy.base
    for prefix in ("owl", "rdf", "rdfs", "xsd", "skos"):
        assert prefixes[prefix] == PREFIXES[prefix]
    # callers get a copy: mutating it must not corrupt the policy
    prefixes["onto"] = "urn:tampered"
    assert policy.prefix_map()["onto"] == policy.base


# ----------------------------------------------------------------------
# version segment stability
# ----------------------------------------------------------------------


def test_version_bump_changes_only_the_version_segment():
    v1 = _policy(version=1)
    v2 = _policy(version=2)

    assert v1.base.endswith("/v1#")
    assert v2.base.endswith("/v2#")
    assert v1.base[:-1].replace("/v1", "/v2") == v2.base[:-1]

    # the local fragment is stable across versions, so diffs stay readable
    assert v1.class_iri("Vehicle").split("#", 1)[1] == (
        v2.class_iri("Vehicle").split("#", 1)[1])


def test_same_name_across_versions_is_not_a_collision():
    for version in (1, 2):
        # rebuild from dict each time: published versions are separate assets
        IriPolicy.for_ontology(_ontology(version=version)).assert_no_collision(
            _ontology(version=version))


# ----------------------------------------------------------------------
# collision detection
# ----------------------------------------------------------------------


def test_class_and_relation_with_the_same_name_collide():
    ontology = _ontology()
    ontology.relations["Vehicle"] = RelationSpec(
        name="Vehicle", source="Vehicle", target="TollStation")

    with pytest.raises(AdapterError) as excinfo:
        IriPolicy.for_ontology(ontology).assert_no_collision(ontology)
    assert "collision" in str(excinfo.value)


def test_distinct_terms_do_not_collide():
    ontology = _ontology()
    policy = IriPolicy.for_ontology(ontology)

    policy.assert_no_collision(ontology)
    fragments = policy.declared_terms(ontology)
    assert fragments["Vehicle"] == "class Vehicle"
    assert fragments["passes"] == "relation passes"
    assert fragments["Vehicle.plate"] == "property Vehicle.plate"
    assert fragments["term.%E8%BD%A6%E8%BE%86"] == "vocabulary term 车辆"


# ----------------------------------------------------------------------
# invalid inputs
# ----------------------------------------------------------------------


def test_empty_workspace_is_rejected():
    with pytest.raises(AdapterError) as excinfo:
        IriPolicy.for_ontology(_ontology(workspace_id=""))
    assert "workspace" in str(excinfo.value)


def test_blank_ontology_name_is_rejected():
    with pytest.raises(AdapterError) as excinfo:
        IriPolicy.for_ontology(_ontology(name="   "))
    assert "ontology name" in str(excinfo.value)


# ----------------------------------------------------------------------
# configurable public base
# ----------------------------------------------------------------------


def test_iri_policy_for_keeps_deterministic_suffix_with_custom_base():
    ontology = _ontology()
    custom = iri_policy_for(ontology, "https://data.example.org")

    assert custom.base == "https://data.example.org/ws/ws-1/onto/traffic/v1#"
    assert custom.class_iri("Vehicle").endswith("#Vehicle")
    assert iri_policy_for(ontology, DEFAULT_BASE) == IriPolicy.for_ontology(ontology)
