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
"""

from __future__ import annotations

from ..capability.model import Capability
from .model import TYPE_KINDS, Ontology
from ...errors import CompilerError
from ...ports.runtime import SchemaPayload


class OntologyCompiler:
    """Pure, deterministic Platform -> Kernel compilation."""

    def compile(self, ontology: Ontology, capabilities: list[Capability]) -> SchemaPayload:
        schema: dict = {
            "ontology": {
                "name": self._schema_name(ontology),
                "version": ontology.version,
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
                "properties": {
                    p.name: self._property(p.name, p.type, otype.name)
                    for p in otype.properties.values()
                },
            }

        for rel in ontology.relations.values():
            schema["relations"][rel.name] = {
                "source": rel.source,
                "target": rel.target,
            }
            if rel.cardinality:
                schema["relations"][rel.name]["cardinality"] = rel.cardinality

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
        else:  # pragma: no cover - guarded by capability validation
            raise CompilerError(
                f"capability {capability.name!r}: unknown implementation kind "
                f"{impl.kind!r}"
            )

    def _property(self, name: str, type_kind: str, otype: str) -> dict:
        if type_kind not in TYPE_KINDS:
            raise CompilerError(
                f"{otype}.{name}: unknown property type {type_kind!r}; "
                f"expected one of {list(TYPE_KINDS)}"
            )
        return {"type": type_kind}

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
