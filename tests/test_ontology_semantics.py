"""0.3/0.4 Ontology semantics: facets, identity, single publish gate.

Plan-v2 acceptance (Phase 0.3/0.4, platform side):
    test_identity_declared_and_compiled
    test_facets_validated_and_compiled
    test_publish_uses_single_rule_set
    test_publish_failure_keeps_validated
    test_projection_id_field_must_match_identity
"""

from __future__ import annotations

import pytest

from onto_platform.domain.ontology.compiler import OntologyCompiler
from onto_platform.domain.ontology.model import (
    ObjectTypeSpec,
    Ontology,
    PropertySpec,
    RelationSpec,
)
from onto_platform.domain.ontology.validation import validate_ontology
from onto_platform.errors import CompilerError, ValidationError


def _station(**overrides) -> ObjectTypeSpec:
    spec = ObjectTypeSpec(
        name="TollStation",
        description="收费站",
        properties={
            "name": PropertySpec(name="name", type="string", required=True),
            "status": PropertySpec(
                name="status", type="string", enum=["free", "congested"]),
        },
    )
    for key, value in overrides.items():
        setattr(spec, key, value)
    return spec


def _vehicle() -> ObjectTypeSpec:
    return ObjectTypeSpec(
        name="Vehicle",
        description="车辆",
        properties={
            "plate": PropertySpec(name="plate", type="string", required=True),
            "balance": PropertySpec(
                name="balance", type="number", minimum=0, maximum=100000,
                unit="CNY", precision=2),
            "kind": PropertySpec(
                name="kind", type="string", enum=["car", "truck"]),
            "status": PropertySpec(
                name="status", type="string", enum=["free", "congested"]),
        },
        identity=["plate"],
        state_machine={
            "field": "status",
            "transitions": {"free": ["congested"], "congested": ["free"]},
        },
    )


def _ontology() -> Ontology:
    ontology = Ontology(id="o1", workspace_id="w1", name="traffic")
    ontology.upsert_object(_vehicle())
    ontology.upsert_object(_station())
    ontology.upsert_relation(
        RelationSpec(name="passes", source="Vehicle", target="TollStation",
                     cardinality="many-to-one"))
    return ontology


def test_validation_accepts_declared_semantics():
    assert validate_ontology(_ontology()) == []


def test_identity_must_name_required_scalar_fields():
    ontology = _ontology()
    vehicle = ontology.objects["Vehicle"]
    vehicle.identity = ["note"]
    vehicle.properties["note"] = PropertySpec(name="note", type="object")
    problems = validate_ontology(ontology)
    assert any("must be string/integer" in p for p in problems)
    assert any("must be required" in p for p in problems)


def test_property_facets_are_type_checked():
    ontology = _ontology()
    vehicle = ontology.objects["Vehicle"]
    vehicle.properties["kind"].enum = [1, 2]          # enum on string
    vehicle.properties["balance"].minimum = 100      # min > max
    vehicle.properties["balance"].maximum = 10
    vehicle.properties["balance"].unit = "CNY"
    vehicle.properties["plate"].unit = "kg"          # unit on string
    problems = validate_ontology(ontology)
    assert any("is not a valid string" in p for p in problems)
    assert any("minimum 100 > maximum 10" in p for p in problems)
    assert any("unit only applies to numeric" in p for p in problems)


def test_state_machine_states_must_be_declared():
    ontology = _ontology()
    ontology.objects["Vehicle"].state_machine = {
        "field": "status", "transitions": {"free": ["ghost"]}}
    problems = validate_ontology(ontology)
    assert any("state 'ghost' is not in the enum" in p for p in problems)


def test_relation_cardinality_and_inverse_are_validated():
    ontology = _ontology()
    ontology.upsert_relation(
        RelationSpec(name="weird", source="Vehicle", target="TollStation",
                     cardinality="many_to_many"))  # legacy alias is accepted
    ontology.upsert_relation(
        RelationSpec(name="bad", source="Vehicle", target="TollStation",
                     cardinality="sometimes"))
    assert any("unknown cardinality 'sometimes'" in p
               for p in validate_ontology(ontology))
    assert not any("many_to_many" in p for p in validate_ontology(ontology))


def test_event_category_requires_occurred_at():
    ontology = _ontology()
    event = ObjectTypeSpec(
        name="Pass", category="event",
        properties={"amount": PropertySpec(name="amount", type="number")})
    ontology.upsert_object(event)
    problems = validate_ontology(ontology)
    assert any("requires a 'occurred_at'" in p for p in problems)


def test_compiler_emits_identity_facets_and_omits_defaults():
    payload = OntologyCompiler().compile(_ontology(), [])
    vehicle = payload.schema["objects"]["Vehicle"]
    assert vehicle["identity"] == ["plate"]
    assert vehicle["category"] == "object"
    assert vehicle["state_machine"]["field"] == "status"
    balance = vehicle["properties"]["balance"]
    assert balance["minimum"] == 0
    assert balance["maximum"] == 100000
    assert balance["unit"] == "CNY"
    assert balance["precision"] == 2
    assert vehicle["properties"]["kind"]["enum"] == ["car", "truck"]
    # defaults stay omitted so the payload remains minimal/stable
    assert "required" not in vehicle["properties"]["kind"]
    assert "enum" not in vehicle["properties"]["balance"]
    assert payload.schema["relations"]["passes"]["cardinality"] == "many-to-one"


def test_legacy_snake_case_cardinality_is_normalized():
    ontology = _ontology()
    ontology.relations["passes"].cardinality = "many_to_one"
    payload = OntologyCompiler().compile(ontology, [])
    assert payload.schema["relations"]["passes"]["cardinality"] == "many-to-one"


def _fresh_workspace(platform) -> tuple[str, str]:
    ws = platform.workspaces.create("semantics", actor="admin")
    wid = ws["id"]
    platform.ontologies.create(wid, "traffic", actor="admin")
    platform.ontologies.upsert_object(
        wid, "admin", "Vehicle",
        properties=[{"name": "plate", "type": "string", "required": True}],
        identity=["plate"])
    return wid, platform.ontologies.get(wid, "admin")["id"]


def test_projection_id_field_must_match_identity(platform):
    wid, _ = _fresh_workspace(platform)
    source = platform.data.connect_source(
        wid, "admin", "vehicles", "static",
        assets=[{"name": "vehicles", "config": {"rows": [{"vid": "V1"}]}}])
    with pytest.raises(ValidationError) as excinfo:
        platform.data.create_projection(
            wid, "admin", "p", source["id"],
            object_mappings=[{
                "asset": "vehicles", "object_type": "Vehicle",
                "id_field": "vid",
                "fields": [{"source_field": "vid", "property": "plate"}],
            }])
    assert "identity field 'plate'" in str(excinfo.value)


def test_publish_uses_single_rule_set(platform):
    wid, _ = _fresh_workspace(platform)
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        properties=[{"name": "name", "type": "string"}])
    platform.ontologies.upsert_relation(
        wid, "admin", "passes", "Vehicle", "TollStation",
        cardinality="sometimes")

    problems = platform.ontologies.validate(wid, "admin")
    assert any("unknown cardinality" in p for p in problems)
    with pytest.raises(ValidationError) as excinfo:
        platform.ontologies.publish(wid, "admin")
    # same rule set: the publish error mentions exactly the validate problem
    assert "unknown cardinality 'sometimes'" in str(excinfo.value)
    assert platform.ontologies.get(wid, "admin")["lifecycle"] == "draft"


def test_publish_failure_keeps_validated(platform):
    wid, _ = _fresh_workspace(platform)
    platform.ontologies.upsert_object(
        wid, "admin", "TollStation",
        properties=[{"name": "name", "type": "string"}])
    assert platform.ontologies.validate(wid, "admin") == []
    assert platform.ontologies.get(wid, "admin")["lifecycle"] == "validated"

    # a published capability with a dangling target makes compilation fail
    platform.ctx.save("capability", {
        "id": "ghost", "workspace_id": wid, "key": "fly", "name": "飞行",
        "target_type": "Airplane", "description": "",
        "inputs": [], "implementation": {"kind": "python", "entrypoint": "/tmp/x.py"},
        "lifecycle": "published", "created_at": "", "updated_at": "",
    })
    with pytest.raises(CompilerError):
        platform.ontologies.publish(wid, "admin")
    # no half-publish: validation state survives, nothing is marked published
    assert platform.ontologies.get(wid, "admin")["lifecycle"] == "validated"

    # clearing the offender lets the exact same publish succeed
    platform.ctx.repo.delete("capability", wid, "ghost")
    result = platform.ontologies.publish(wid, "admin")
    assert result["ontology"]["lifecycle"] == "published"
    assert result["ontology"]["schema_version"] == result["ontology"]["version"]
    assert {o["name"] for o in result["runtime"]["objects"]} >= {
        "Vehicle", "TollStation"}
