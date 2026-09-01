"""Data: connecting the real world to the ontology world (design.txt §11-§16).

    DataSource   现实数据从哪来 (Database / API / ES / File / MQ)
    DataAsset    源中的一个逻辑数据集 (表 / 查询 / 文件)
    Mapping      字段 <-> 业务语义的对应规则
    Projection   从源数据构造语义状态 (Source State -> Semantic State)

Mapping = 字段/语义对应；Projection = 语义构造配方。两者是不同层次，
但 Mapping 脱离 Projection 没有意义，因此 v0.1 中 Mapping 作为
Projection 聚合内部的价值对象存在。

Data 不直接操作 Kernel Store，统一走 RuntimeGateway (design.txt §15)；
Kernel 是 Semantic State 的唯一事实来源 (design.txt §16)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...errors import ValidationError

DATASOURCE_KINDS = ("static", "sqlite", "rest")


@dataclass(slots=True)
class DataAsset:
    """One logical dataset inside a source.

    config is kind-specific, e.g.:
        static: {"rows": [{...}, ...]}
        sqlite: {"query": "SELECT ..."}
        rest:   {"path": "/vehicles", "params": {...}}
    """

    name: str
    config: dict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "config": dict(self.config),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataAsset":
        return cls(
            name=data["name"],
            config=dict(data.get("config", {})),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class DataSource:
    """Infrastructure-agnostic connection to reality."""

    id: str
    workspace_id: str
    name: str
    kind: str  # static | sqlite | rest
    config: dict = field(default_factory=dict)
    assets: dict[str, DataAsset] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "kind": self.kind,
            "config": dict(self.config),
            "assets": {k: a.to_dict() for k, a in self.assets.items()},
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataSource":
        return cls(
            id=data["id"],
            workspace_id=data["workspace_id"],
            name=data["name"],
            kind=data["kind"],
            config=dict(data.get("config", {})),
            assets={k: DataAsset.from_dict(v) for k, v in data.get("assets", {}).items()},
            created_at=data.get("created_at", ""),
        )


# ----------------------------------------------------------------------
# Mapping (value objects inside the Projection aggregate)
# ----------------------------------------------------------------------


@dataclass(slots=True)
class FieldMapping:
    """source field -> ObjectType.property (design.txt §13)."""

    source_field: str
    property: str

    def to_dict(self) -> dict:
        return {"source_field": self.source_field, "property": self.property}

    @classmethod
    def from_dict(cls, data: dict) -> "FieldMapping":
        return cls(source_field=data["source_field"], property=data["property"])


@dataclass(slots=True)
class ObjectMapping:
    """How rows of one asset become objects of one ObjectType."""

    asset: str
    object_type: str
    id_field: str                     # row field providing the object id
    fields: list[FieldMapping] = field(default_factory=list)
    filter: dict = field(default_factory=dict)  # row-level prefilter
    exclude_missing_id: bool = True

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "object_type": self.object_type,
            "id_field": self.id_field,
            "fields": [f.to_dict() for f in self.fields],
            "filter": dict(self.filter),
            "exclude_missing_id": self.exclude_missing_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ObjectMapping":
        return cls(
            asset=data["asset"],
            object_type=data["object_type"],
            id_field=data["id_field"],
            fields=[FieldMapping.from_dict(f) for f in data.get("fields", [])],
            filter=dict(data.get("filter", {})),
            exclude_missing_id=bool(data.get("exclude_missing_id", True)),
        )


@dataclass(slots=True)
class RelationMapping:
    """How rows of one asset assert relation edges.

    The relation definition (source/target types) comes from the
    ontology; the row only provides the two endpoint ids.
    """

    asset: str
    relation: str
    source_field: str  # row field holding the SOURCE object id
    target_field: str  # row field holding the TARGET object id
    filter: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "relation": self.relation,
            "source_field": self.source_field,
            "target_field": self.target_field,
            "filter": dict(self.filter),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelationMapping":
        return cls(
            asset=data["asset"],
            relation=data["relation"],
            source_field=data["source_field"],
            target_field=data["target_field"],
            filter=dict(data.get("filter", {})),
        )


@dataclass(slots=True)
class Projection:
    """The recipe: Source State -> Semantic State (design.txt §14)."""

    id: str
    workspace_id: str
    name: str
    datasource_id: str
    object_mappings: list[ObjectMapping] = field(default_factory=list)
    relation_mappings: list[RelationMapping] = field(default_factory=list)
    lifecycle: str = "draft"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "datasource_id": self.datasource_id,
            "object_mappings": [m.to_dict() for m in self.object_mappings],
            "relation_mappings": [m.to_dict() for m in self.relation_mappings],
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Projection":
        return cls(
            id=data["id"],
            workspace_id=data["workspace_id"],
            name=data["name"],
            datasource_id=data["datasource_id"],
            object_mappings=[ObjectMapping.from_dict(m) for m in data.get("object_mappings", [])],
            relation_mappings=[
                RelationMapping.from_dict(m) for m in data.get("relation_mappings", [])
            ],
            lifecycle=data.get("lifecycle", "draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


def validate_projection(projection: Projection, datasource: DataSource) -> list[str]:
    problems: list[str] = []
    if not projection.object_mappings and not projection.relation_mappings:
        problems.append("projection has no mappings")
    known_assets = set(datasource.assets)
    for m in projection.object_mappings:
        if m.asset not in known_assets:
            problems.append(f"object mapping {m.object_type!r}: unknown asset {m.asset!r}")
        if not m.id_field:
            problems.append(f"object mapping {m.object_type!r}: missing id_field")
    for m in projection.relation_mappings:
        if m.asset not in known_assets:
            problems.append(f"relation mapping {m.relation!r}: unknown asset {m.asset!r}")
    return problems
