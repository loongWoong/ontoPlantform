"""Ontology Compiler: Platform Ontology -> Kernel OntologySchema.

This is THE anti-corruption layer between the two systems (design.txt §10):

    Platform Ontology  --compile-->  kernel parse_schema dict

The compiler:
    解析 / 引用解析 / 语义校验 / 类型转换 / 裁剪平台属性 / 生成 Runtime Schema

Therefore the Kernel never sees platform-internal concepts such as
Vocabulary, lifecycle, workspace ids or capability ids.  Published
Capabilities are compiled into kernel Functions here as well
(``{ObjectType}.{capability key}``), with workflow implementations
carried beside the payload (the kernel only sees a binding URI).

v0.2 also carries the decidable semantics: identity criteria, date/time
temporal modes, property facets, enumerated cardinality and inverse
relations.  Defaults are omitted so the payload stays minimal and stable
(the kernel fills them in).
"""

from __future__ import annotations

from ..capability.model import Capability
from .model import TYPE_KINDS, Ontology, normalize_cardinality
from ...errors import CompilerError
from ...ports.runtime import SchemaPayload


class OntologyCompiler:
    """Pure, deterministic Platform -> Kernel compilation."""

    def compile(self, ontology: Ontology, capabilities: list[Capability]) -> SchemaPayload:
        schema: dict = {
            "ontology": {
                "name": self._schema_name(ontology),
                "version": ontology.version,
                "schema_version": ontology.schema_version,
                "profile": ontology.profile,
            },
            "objects": {},
            "relations": {},
            "functions": {},
        }
        bindings: dict = {}
        workflows: dict[str, list[dict]] = {}

        for otype in ontology.objects.values():
            schema["objects"][otype.name] = {
                "description": otype.description,
                "identity": list(otype.identity),
                "category": otype.category,
                "properties": {
                    p.name: self._property(p, otype.name)
                    for p in otype.properties.values()
                },
            }
            if otype.state_machine:
                schema["objects"][otype.name]["state_machine"] = dict(
                    otype.state_machine
                )

        for rel in ontology.relations.values():
            self._check_relation(rel)
            entry: dict = {"source": rel.source, "target": rel.target}
            cardinality = normalize_cardinality(rel.cardinality)
            if cardinality:
                entry["cardinality"] = cardinality
            if rel.inverse:
                entry["inverse"] = rel.inverse
            schema["relations"][rel.name] = entry

        for capability in capabilities:
            self._compile_capability(
                capability, schema, bindings, workflows
            )

        return SchemaPayload(schema=schema, bindings=bindings, workflows=workflows)

    # ------------------------------------------------------------------

    def _compile_capability(
        self,
        capability: Capability,
        schema: dict,
        bindings: dict,
        workflows: dict[str, list[dict]],
    ) -> None:
        if capability.target_type not in schema["objects"]:
            raise CompilerError(
                f"capability {capability.name!r}: target object type "
                f"{capability.target_type!r} is not part of the ontology"
            )
        function_name = capability.function_name
        schema["functions"][function_name] = {
            "target": capability.target_type,
            "description": capability.description or capability.name,
            "input": {
                item.name: {"type": self._input_type(item.type, function_name)}
                for item in capability.inputs
            },
        }
        impl = capability.implementation
        if impl.kind == "python":
            if not impl.entrypoint:
                raise CompilerError(
                    f"capability {capability.name!r}: python implementation "
                    "has no materialized entrypoint (publish it first)"
                )
            bindings[function_name] = {
                "executor": "python",
                "entrypoint": impl.entrypoint,
            }
        elif impl.kind == "workflow":
            uri = f"workflow://{capability.id}"
            bindings[function_name] = {"executor": "workflow", "entrypoint": uri}
            workflows[uri] = [dict(step) for step in impl.steps]
        elif impl.kind == "agent":
            uri = f"agent://{capability.id}"
            bindings[function_name] = {"executor": "agent", "entrypoint": uri}
            # the runner itself is registered by the application layer at
            # publish time (it needs the LLM provider, which is never
            # part of the compiled payload)
        else:  # pragma: no cover - guarded by capability validation
            raise CompilerError(
                f"capability {capability.name!r}: unknown implementation kind "
                f"{impl.kind!r}"
            )

    def _check_relation(self, rel) -> None:
        from .model import CARDINALITIES

        if normalize_cardinality(rel.cardinality) not in CARDINALITIES:
            raise CompilerError(
                f"relation {rel.name!r}: unknown cardinality {rel.cardinality!r}"
            )

    def _property(self, prop, otype: str) -> dict:
        if prop.type not in TYPE_KINDS:
            raise CompilerError(
                f"{otype}.{prop.name}: unknown property type {prop.type!r}; "
                f"expected one of {list(TYPE_KINDS)}"
            )
        entry: dict = {"type": prop.type}
        if prop.required:
            entry["required"] = True
        if prop.enum:
            entry["enum"] = list(prop.enum)
        if prop.minimum is not None:
            entry["minimum"] = prop.minimum
        if prop.maximum is not None:
            entry["maximum"] = prop.maximum
        if prop.unique:
            entry["unique"] = True
        if prop.unit:
            entry["unit"] = prop.unit
        if prop.precision is not None:
            entry["precision"] = prop.precision
        if prop.default is not None:
            entry["default"] = prop.default
        if prop.temporal:
            entry["temporal"] = prop.temporal
        return entry

    def _input_type(self, type_kind: str, function_name: str) -> str:
        if type_kind not in TYPE_KINDS:
            raise CompilerError(
                f"{function_name}: unknown input type {type_kind!r}; "
                f"expected one of {list(TYPE_KINDS)}"
            )
        return type_kind

    def _schema_name(self, ontology: Ontology) -> str:
        # kernel schema names are simple identifiers; keep them stable
        name = "".join(ch if ch.isalnum() else "_" for ch in ontology.name)
        return name or f"ontology_{ontology.id}"
