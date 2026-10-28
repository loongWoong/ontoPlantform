"""Ontology Compiler: Platform model -> kernel-compatible payload (§10)."""

from __future__ import annotations

import pytest

from onto_platform.domain.capability.model import (
    Capability,
    CapabilityInput,
    Implementation,
)
from onto_platform.domain.ontology.compiler import OntologyCompiler
from onto_platform.domain.ontology.model import (
    Ontology,
    ObjectTypeSpec,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from onto_platform.errors import CompilerError


def build_ontology() -> Ontology:
    ontology = Ontology(id="o1", workspace_id="w1", name="traffic", version=1)
    ontology.upsert_object(ObjectTypeSpec(
        name="Vehicle", description="车辆",
        properties={"plate": PropertySpec(name="plate", type="string"),
                    "balance": PropertySpec(name="balance", type="number")}))
    ontology.upsert_object(ObjectTypeSpec(
        name="TollStation", description="收费站",
        properties={"name": PropertySpec(name="name", type="string")}))
    ontology.upsert_relation(RelationSpec(name="passes", source="Vehicle",
                                          target="TollStation"))
    # Vocabulary must NOT leak into the kernel payload (§8)
    ontology.add_term(VocabularyTerm(term="收费站", concept="TollStation"))
    return ontology


def test_compiles_to_kernel_schema_dict():
    payload = OntologyCompiler().compile(build_ontology(), [])
    assert payload.schema["ontology"] == {
        "name": "traffic",
        "version": 1,
        "schema_version": 1,
        "profile": "closed",
    }
    assert payload.schema["objects"]["Vehicle"]["properties"]["balance"] == {"type": "number"}
    assert payload.schema["relations"]["passes"] == {"source": "Vehicle", "target": "TollStation"}
    # platform-only concepts are stripped
    assert "vocabulary" not in payload.schema
    assert "workspace_id" not in payload.schema


def test_kernel_loader_accepts_compiled_payload():
    """The compiler output must survive the kernel's own parse_schema."""
    from ontology_runtime.loader import parse_schema

    payload = OntologyCompiler().compile(build_ontology(), [])
    schema, catalog = parse_schema(payload.schema)
    assert set(schema.objects) == {"Vehicle", "TollStation"}
    assert schema.relations["passes"].source == "Vehicle"
    assert catalog.functions == {}


def test_capability_compiles_into_kernel_function():
    ontology = build_ontology()
    capability = Capability(
        id="c1", workspace_id="w1", key="recharge", name="车辆充值",
        target_type="Vehicle", description="给车辆充值",
        inputs=[CapabilityInput(name="amount", type="number")],
        implementation=Implementation(kind="python", entrypoint="/tmp/recharge.py"),
    )
    payload = OntologyCompiler().compile(ontology, [capability])
    assert payload.schema["functions"]["Vehicle.recharge"] == {
        "target": "Vehicle",
        "description": "给车辆充值",
        "input": {"amount": {"type": "number"}},
    }
    assert payload.bindings["Vehicle.recharge"] == {
        "executor": "python", "entrypoint": "/tmp/recharge.py"
    }


def test_workflow_capability_binds_by_uri():
    ontology = build_ontology()
    capability = Capability(
        id="c9", workspace_id="w1", key="report", name="报告",
        target_type="TollStation", description="report",
        implementation=Implementation(kind="workflow", steps=(
            {"kind": "related", "ref": "{{target}}", "relation": "passes"},
        )),
    )
    payload = OntologyCompiler().compile(ontology, [capability])
    binding = payload.bindings["TollStation.report"]
    assert binding["executor"] == "workflow"
    assert payload.workflows[binding["entrypoint"]] == [
        {"kind": "related", "ref": "{{target}}", "relation": "passes"}
    ]


def test_unknown_target_type_is_a_compiler_error():
    ontology = build_ontology()
    capability = Capability(
        id="c2", workspace_id="w1", key="fly", name="飞行",
        target_type="Airplane", description="",
        implementation=Implementation(kind="python", entrypoint="/tmp/x.py"),
    )
    with pytest.raises(CompilerError):
        OntologyCompiler().compile(ontology, [capability])


def test_unknown_property_type_is_a_compiler_error():
    ontology = build_ontology()
    ontology.objects["Vehicle"].properties["plate"].type = "float64"
    with pytest.raises(CompilerError):
        OntologyCompiler().compile(ontology, [])
