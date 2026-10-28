"""Application: the top product abstraction for end users (design.txt §21-§23).

用户创建的是"应用"，而不是 Workflow / Function / Tool。
内部组成 (Views / Queries / Capabilities) 对用户默认隐藏。

    View   = Ontology -> UI 的投影 (Object->Card, Relation->Table, ...)
    Query  = 命名的语义查询 (find + optional relation expansion)
"""

from __future__ import annotations

from dataclasses import dataclass, field

VIEW_KINDS = ("table", "card", "chart", "map")


@dataclass(slots=True)
class View:
    """Ontology -> UI projection."""

    alias: str
    title: str
    kind: str = "table"        # table | card | chart | map
    object_type: str = ""
    properties: list[str] = field(default_factory=list)
    query_alias: str = ""      # optional: drive the view from a named query

    def to_dict(self) -> dict:
        return {
            "alias": self.alias,
            "title": self.title,
            "kind": self.kind,
            "object_type": self.object_type,
            "properties": list(self.properties),
            "query_alias": self.query_alias,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "View":
        return cls(
            alias=data["alias"],
            title=data.get("title", data.get("alias", "")),
            kind=data.get("kind", "table"),
            object_type=data.get("object_type", ""),
            properties=list(data.get("properties", [])),
            query_alias=data.get("query_alias", ""),
        )


@dataclass(slots=True)
class QuerySpec:
    """A named semantic query over one object type."""

    alias: str
    title: str = ""
    object_type: str = ""
    filters: dict = field(default_factory=dict)
    expand: list[dict] = field(default_factory=list)  # [{relation, direction}]

    @staticmethod
    def coerce_expand(expand) -> list[dict]:
        """Accept the convenient shapes clients actually send.

        "passes" / ["passes"] -> [{"relation": "passes", "direction": "out"}];
        dicts are normalized to exactly relation+direction so to_dict can
        never crash on a stray string (a plain dict("passes") raises).
        """
        normalized: list[dict] = []
        for entry in expand or []:
            if isinstance(entry, str):
                if entry.strip():
                    normalized.append(
                        {"relation": entry.strip(), "direction": "out"}
                    )
            elif isinstance(entry, dict) and str(entry.get("relation", "")).strip():
                normalized.append({
                    "relation": str(entry["relation"]).strip(),
                    "direction": str(entry.get("direction", "out")) or "out",
                })
        return normalized

    def to_dict(self) -> dict:
        return {
            "alias": self.alias,
            "title": self.title,
            "object_type": self.object_type,
            "filters": dict(self.filters),
            "expand": [dict(e) for e in self.expand],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QuerySpec":
        return cls(
            alias=data["alias"],
            title=data.get("title", ""),
            object_type=data.get("object_type", ""),
            filters=dict(data.get("filters", {})),
            expand=cls.coerce_expand(data.get("expand", [])),
        )


@dataclass(slots=True)
class Application:
    id: str
    workspace_id: str
    name: str
    description: str = ""
    views: dict[str, View] = field(default_factory=dict)
    queries: dict[str, QuerySpec] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)  # capability ids
    # agent entry points (design.txt §50: Application = View/Query/Agent;
    # one Agent domain lives in Intelligence - the application only
    # references it, never duplicates it)
    agents: list[dict] = field(default_factory=list)
    # business objective (plan-v2 2.1): {metric,target,band,window,direction,samples}
    objective: dict = field(default_factory=dict)
    lifecycle: str = "draft"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description,
            "views": {k: v.to_dict() for k, v in self.views.items()},
            "queries": {k: q.to_dict() for k, q in self.queries.items()},
            "capabilities": list(self.capabilities),
            "agents": [dict(a) for a in self.agents],
            "objective": dict(self.objective),
            "lifecycle": self.lifecycle,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Application":
        return cls(
            id=data["id"],
            workspace_id=data["workspace_id"],
            name=data["name"],
            description=data.get("description", ""),
            views={k: View.from_dict(v) for k, v in data.get("views", {}).items()},
            queries={k: QuerySpec.from_dict(q) for k, q in data.get("queries", {}).items()},
            capabilities=list(data.get("capabilities", [])),
            agents=[dict(a) for a in data.get("agents", [])],
            objective=dict(data.get("objective") or {}),
            lifecycle=data.get("lifecycle", "draft"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
