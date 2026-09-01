"""Ontology: the platform's central semantic model.

用户通过 Ontology 定义：这个业务世界里有什么东西，以及它们之间是什么
关系 (design.txt §5)。Ontology 只描述结构，不吞并 Data / Capability /
Application。

    Ontology
    ├── ObjectType  (+ Property)
    ├── Relation
    └── Vocabulary   (业务语言 -> 本体概念, 只属于 Platform)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import NotFoundError, ValidationError

# Platform-side scalar type system, aligned 1:1 with the kernel's
# TypeDef kinds.  The compiler rejects anything else before the kernel
# ever sees the payload.
TYPE_KINDS: tuple[str, ...] = (
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "datetime",
)


@dataclass(slots=True)
class PropertySpec:
    """A property of a platform ObjectType."""

    name: str
    type: str = "string"
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> "PropertySpec":
        return cls(
            name=data["name"],
            type=data.get("type", "string"),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class ObjectTypeSpec:
    """A kind of business object, e.g. ``Vehicle`` (车辆)."""

    name: str
    description: str = ""
    properties: dict[str, PropertySpec] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "properties": {k: p.to_dict() for k, p in self.properties.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ObjectTypeSpec":
        raw = data.get("properties", {})
        items = raw.values() if isinstance(raw, dict) else raw
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            properties={item["name"]: PropertySpec.from_dict(item) for item in items},
        )


@dataclass(slots=True)
class RelationSpec:
    """A semantic relation: ``Vehicle --passes--> TollStation``."""

    name: str
    source: str
    target: str
    description: str = ""
    cardinality: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "target": self.target,
            "description": self.description,
            "cardinality": self.cardinality,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationSpec":
        return cls(
            name=data["name"],
            source=data["source"],
            target=data["target"],
            description=data.get("description", ""),
            cardinality=data.get("cardinality", ""),
        )


@dataclass(slots=True)
class VocabularyTerm:
    """业务语言 -> 本体概念 (design.txt §8)。

    LLM 理解业务语义的重要基础；只属于 Platform，不进入 Kernel。
    """

    term: str            # "收费站" / "Toll Station"
    concept: str         # "TollStation" (object type) or "TollStation.status"
    kind: str = "object"  # "object" | "property" | "relation"
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "concept": self.concept,
            "kind": self.kind,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VocabularyTerm":
        return cls(
            term=data["term"],
            concept=data["concept"],
            kind=data.get("kind", "object"),
            aliases=list(data.get("aliases", [])),
        )


@dataclass(slots=True)
class Ontology:
    """Design-time asset: Draft -> Validate -> Publish -> Runtime Schema."""

    id: str
    workspace_id: str
    name: str
    description: str = ""
    version: int = 1
    objects: dict[str, ObjectTypeSpec] = field(default_factory=dict)
    relations: dict[str, RelationSpec] = field(default_factory=dict)
    vocabulary: dict[str, VocabularyTerm] = field(default_factory=dict)
    lifecycle: str = "draft"
    created_at: str = ""
    updated_at: str = ""

    # ---- aggregate editing ----

    def upsert_object(self, spec: ObjectTypeSpec) -> None:
        self._ensure_editable()
        _ensure_identifier(spec.name, "object type")
        self.objects[spec.name] = spec

    def remove_object(self, name: str) -> None:
        self._ensure_editable()
        if name not in self.objects:
            raise NotFoundError(f"object type not found: {name!r}")
        dependent = [
            r.name for r in self.relations.values() if name in (r.source, r.target)
        ]
        if dependent:
            raise ValidationError(
                f"object type {name!r} is used by relations {dependent}; "
                "remove them first"
            )
        del self.objects[name]

    def upsert_relation(self, spec: RelationSpec) -> None:
        self._ensure_editable()
        _ensure_identifier(spec.name, "relation")
        if spec.source not in self.objects:
            raise ValidationError(
                f"relation {spec.name!r}: source {spec.source!r} is not a defined object type"
            )
        if spec.target not in self.objects:
            raise ValidationError(
                f"relation {spec.name!r}: target {spec.target!r} is not a defined object type"
            )
        self.relations[spec.name] = spec

    def remove_relation(self, name: str) -> None:
        self._ensure_editable()
        if name not in self.relations:
            raise NotFoundError(f"relation not found: {name!r}")
        del self.relations[name]

    def add_term(self, term: VocabularyTerm) -> None:
        self._ensure_editable()
        if term.kind == "object" and term.concept not in self.objects:
            raise ValidationError(
                f"vocabulary term {term.term!r}: concept {term.concept!r} "
                "is not a defined object type"
            )
        if term.kind == "relation" and term.concept not in self.relations:
            raise ValidationError(
                f"vocabulary term {term.term!r}: concept {term.concept!r} "
                "is not a defined relation"
            )
        if term.kind == "property":
            otype, _, prop = term.concept.partition(".")
            if otype not in self.objects or prop not in self.objects[otype].properties:
                raise ValidationError(
                    f"vocabulary term {term.term!r}: concept {term.concept!r} "
                    "is not a defined property"
                )
        self.vocabulary[term.term] = term

    # ---- helpers ----

    def _ensure_editable(self) -> None:
        if self.lifecycle == "published":
            raise ValidationError(
                "published ontology is immutable; create a new version or "
                "deprecate it first"
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "objects": {k: o.to_dict() for k, o in self.objects.items()},
            "relations": {k: r.to_dict() for k, r in self.relations.items()},
            "vocabulary": {k: v.to_dict() for k, v in self.vocabulary.items()},
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Ontology":
        return cls(
            id=data["id"],
            workspace_id=data["workspace_id"],
            name=data["name"],
            description=data.get("description", ""),
            version=int(data.get("version", 1)),
            objects={
                k: ObjectTypeSpec.from_dict(v) for k, v in data.get("objects", {}).items()
            },
            relations={
                k: RelationSpec.from_dict(v) for k, v in data.get("relations", {}).items()
            },
            vocabulary={
                k: VocabularyTerm.from_dict(v) for k, v in data.get("vocabulary", {}).items()
            },
            lifecycle=data.get("lifecycle", "draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


def _ensure_identifier(name: str, what: str) -> None:
    if not name or not name.replace("_", "").isalnum() or name[0].isdigit():
        raise ValidationError(
            f"{what} name {name!r} must be an identifier (letters/digits/underscore)"
        )
