"""Ontology: the platform's central semantic model.

用户通过 Ontology 定义：这个业务世界里有什么东西，以及它们之间是什么
关系 (design.txt §5)。Ontology 只描述结构，不吞并 Data / Capability /
Application。

    Ontology
    ├── ObjectType  (+ Property, identity, category)
    ├── Relation    (+ cardinality, inverse)
    └── Vocabulary   (业务语言 -> 本体概念, 只属于 Platform)

v0.2 adds the minimal *decidable* semantics the control loop needs:
identity criteria, property facets (required/enum/range/unit/unique),
enumerated cardinality and a declared logical profile (closed/open world).
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

# relation multiplicity, source -> target
CARDINALITIES: tuple[str, ...] = (
    "",
    "one-to-one",
    "one-to-many",
    "many-to-one",
    "many-to-many",
)

# older drafts/examples used snake_case; accept it and normalize on compile
_CARDINALITY_ALIASES = {
    "one_to_one": "one-to-one",
    "one_to_many": "one-to-many",
    "many_to_one": "many-to-one",
    "many_to_many": "many-to-many",
}


def normalize_cardinality(value: str) -> str:
    return _CARDINALITY_ALIASES.get(value, value)

# object meta-category: ordinary object / event (happened) / value (immutable)
CATEGORIES: tuple[str, ...] = ("object", "event", "value")

# logical profile: closed world (absence = false) vs open world (absence = unknown)
PROFILES: tuple[str, ...] = ("closed", "open")

TEMPORAL_MODES: tuple[str, ...] = ("", "snapshot", "history")


@dataclass(slots=True)
class PropertySpec:
    """A property of a platform ObjectType, with optional facets."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    enum: list = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    unique: bool = False
    unit: str = ""
    precision: int | None = None
    default: object = None
    temporal: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
            "enum": list(self.enum),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unique": self.unique,
            "unit": self.unit,
            "precision": self.precision,
            "default": self.default,
            "temporal": self.temporal,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PropertySpec":
        return cls(
            name=data["name"],
            type=data.get("type", "string"),
            description=data.get("description", ""),
            required=bool(data.get("required", False)),
            enum=list(data.get("enum") or []),
            minimum=_optional_number(data.get("minimum")),
            maximum=_optional_number(data.get("maximum")),
            unique=bool(data.get("unique", False)),
            unit=str(data.get("unit") or ""),
            precision=(
                int(data["precision"])
                if data.get("precision") is not None
                else None
            ),
            default=data.get("default"),
            temporal=str(data.get("temporal") or ""),
        )


@dataclass(slots=True)
class ObjectTypeSpec:
    """A kind of business object, e.g. ``Vehicle`` (车辆).

    ``identity`` is the business key deciding whether two references are the
    same entity.  ``category`` separates objects from events and values.
    ``state_machine`` is an optional declared lifecycle:
    ``{"field": "status", "transitions": {"free": ["congested"], ...}}``.
    """

    name: str
    description: str = ""
    properties: dict[str, PropertySpec] = field(default_factory=dict)
    identity: list[str] = field(default_factory=list)
    category: str = "object"
    state_machine: dict | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "properties": {k: p.to_dict() for k, p in self.properties.items()},
            "identity": list(self.identity),
            "category": self.category,
            "state_machine": dict(self.state_machine) if self.state_machine else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ObjectTypeSpec":
        raw = data.get("properties", {})
        items = raw.values() if isinstance(raw, dict) else raw
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            properties={item["name"]: PropertySpec.from_dict(item) for item in items},
            identity=list(data.get("identity") or []),
            category=data.get("category", "object"),
            state_machine=data.get("state_machine"),
        )


@dataclass(slots=True)
class RelationSpec:
    """A semantic relation: ``Vehicle --passes--> TollStation``."""

    name: str
    source: str
    target: str
    description: str = ""
    cardinality: str = ""
    inverse: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "target": self.target,
            "description": self.description,
            "cardinality": self.cardinality,
            "inverse": self.inverse,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationSpec":
        return cls(
            name=data["name"],
            source=data["source"],
            target=data["target"],
            description=data.get("description", ""),
            cardinality=data.get("cardinality", ""),
            inverse=data.get("inverse", ""),
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
    schema_version: int = 1
    # revision lineage (plan-v2 2.5): the published version this revision copies
    parent_version: int | None = None
    # stable hash of the compiled schema written at publish time
    schema_fingerprint: str = ""
    profile: str = "closed"
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

    # ---- publish gate ----

    def assert_publishable(self) -> None:
        """Single publish precondition: raise with every problem found.

        Both the validate and publish use cases call this exactly once, so
        the rules cannot drift between two copies of validation logic.
        """
        from .validation import validate_ontology

        problems = validate_ontology(self)
        if problems:
            raise ValidationError(
                "ontology validation failed: " + "; ".join(problems)
            )

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
            "schema_version": self.schema_version,
            "parent_version": self.parent_version,
            "schema_fingerprint": self.schema_fingerprint,
            "profile": self.profile,
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
            schema_version=int(data.get("schema_version", 1)),
            parent_version=(int(data["parent_version"])
                            if data.get("parent_version") is not None else None),
            schema_fingerprint=str(data.get("schema_fingerprint") or ""),
            profile=data.get("profile", "closed"),
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


def _optional_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValidationError(f"numeric facet must be a number, got bool")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"numeric facet must be a number, got {value!r}") from None
