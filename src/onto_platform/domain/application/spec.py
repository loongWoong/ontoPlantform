"""Application orchestration contracts (P1): ApplicationSpec / ViewSpec / InteractionSpec.

The P0 ``Application`` aggregate mixes observation surfaces (``views``) with
operation surfaces (``capabilities``/``agents``) and has no single object that
states the whole product.  This module lifts it into explicit, read-only
contracts (docs/analysis/ontology-driven-application-architecture.md §3.3,
§4.3, §7.3):

* :class:`ViewSpec` -- an observation surface: data binding, layout, permission
  cropping.  ``table``/``card``/``chart``/``map`` are the P0 kinds; ``graph``,
  ``dashboard``, ``timeline`` and ``spatial`` are the richer surfaces the
  analysis calls for (Digital Twin is deliberately *not* a kind yet: it needs
  live state + simulation).
* :class:`InteractionSpec` -- an operation surface: ``action`` (a capability
  invocation), ``workflow`` (process), ``form`` (input), ``agent`` (conversation).
  Execution stays in the runtime/capability domain; this is only the declaration.
* :class:`ApplicationSpec` -- the composition of both plus the existing
  queries/agents/capabilities/ui artifact, with :meth:`ApplicationSpec.diagnostics`
  reporting dangling references instead of raising (a console/CI can render the
  list; validation stays advisory until publish).

Pure domain: standard library + ``domain`` only.  No repository, no I/O, no
third-party imports, so Smart UI generators and API/CLI can reuse it freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Application, View

#: Observation surfaces a view may project.  P0 ``VIEW_KINDS`` stays the
#: compatible subset used by existing validation; the spec adds the richer
#: surfaces from the target application architecture.
VIEW_SURFACES: tuple[str, ...] = (
    "table",
    "card",
    "chart",
    "map",
    "graph",
    "dashboard",
    "timeline",
    "spatial",
)

#: Operation surfaces: what a user can *do* from the application.
INTERACTION_KINDS: tuple[str, ...] = ("action", "workflow", "form", "agent")

#: Kinds that cannot be declared without a capability to execute.
_CAPABILITY_BACKED = ("action", "form")

#: Rendering config each visual surface needs to be drawable.
_VIEW_CONFIG_REQUIRED: dict[str, tuple[str, ...]] = {
    "chart": ("x", "y"),
    "map": ("lat", "lng"),
}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _mapping(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(slots=True)
class ViewSpec:
    """Ontology -> observation surface contract (what to show and how)."""

    alias: str
    title: str = ""
    kind: str = "table"
    object_type: str = ""
    query_alias: str = ""
    properties: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    layout: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)

    @classmethod
    def from_view(cls, view: View) -> "ViewSpec":
        """Lift an existing P0 ``View`` into the richer spec (lossless)."""
        return cls(
            alias=view.alias,
            title=view.title,
            kind=view.kind,
            object_type=view.object_type,
            query_alias=view.query_alias,
            properties=list(view.properties),
            config=dict(view.config),
        )

    def to_dict(self) -> dict:
        return {
            "alias": self.alias,
            "title": self.title,
            "kind": self.kind,
            "object_type": self.object_type,
            "query_alias": self.query_alias,
            "properties": list(self.properties),
            "config": dict(self.config),
            "layout": dict(self.layout),
            "permissions": dict(self.permissions),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ViewSpec":
        return cls(
            alias=_text(data.get("alias")),
            title=_text(data.get("title", data.get("alias", ""))),
            kind=_text(data.get("kind", "table")) or "table",
            object_type=_text(data.get("object_type", "")),
            query_alias=_text(data.get("query_alias", "")),
            properties=[_text(item) for item in (data.get("properties") or [])],
            config=_mapping(data.get("config")),
            layout=_mapping(data.get("layout")),
            permissions=_mapping(data.get("permissions")),
        )

    def validate(self) -> list[str]:
        """Return contract problems (empty = valid); never raises."""
        problems: list[str] = []
        if not _text(self.alias).strip():
            problems.append("view alias is required")
        if self.kind not in VIEW_SURFACES:
            problems.append(
                f"view {self.alias!r}: unknown kind {self.kind!r}; "
                f"expected one of {list(VIEW_SURFACES)}")
            return problems
        required = _VIEW_CONFIG_REQUIRED.get(self.kind, ())
        config = self.config if isinstance(self.config, dict) else {}
        for key in required:
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(
                    f"view {self.alias!r}: {self.kind} needs config.{key} "
                    "naming a property")
        return problems


@dataclass(slots=True)
class InteractionSpec:
    """Ontology -> operation surface contract (what a user can do)."""

    id: str
    kind: str
    title: str = ""
    capability_id: str = ""
    config: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    approval: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "capability_id": self.capability_id,
            "config": dict(self.config),
            "permissions": dict(self.permissions),
            "approval": dict(self.approval),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InteractionSpec":
        return cls(
            id=_text(data.get("id")),
            kind=_text(data.get("kind")),
            title=_text(data.get("title", "")),
            capability_id=_text(data.get("capability_id", "")),
            config=_mapping(data.get("config")),
            permissions=_mapping(data.get("permissions")),
            approval=_mapping(data.get("approval")),
        )

    def validate(self) -> list[str]:
        """Return contract problems (empty = valid); never raises."""
        problems: list[str] = []
        interaction_id = self.id.strip()
        if not interaction_id:
            problems.append("interaction id is required")
        if self.kind not in INTERACTION_KINDS:
            problems.append(
                f"interaction {self.id!r}: unknown kind {self.kind!r}; "
                f"expected one of {list(INTERACTION_KINDS)}")
        elif self.kind in _CAPABILITY_BACKED and not self.capability_id.strip():
            problems.append(
                f"interaction {self.id!r}: {self.kind} needs a capability_id")
        for label, value in (
            ("config", self.config),
            ("permissions", self.permissions),
            ("approval", self.approval),
        ):
            if not isinstance(value, dict):
                problems.append(
                    f"interaction {self.id!r}: {label} must be a dict")
        return problems


@dataclass(slots=True)
class ApplicationSpec:
    """The whole application as one declarative contract."""

    application_id: str
    workspace_id: str
    name: str
    description: str = ""
    #: Contract revision of the spec itself.  ``Application`` has no schema
    #: version yet, so the P1 spec starts at 1 (documented in the report).
    version: int = 1
    lifecycle: str = "draft"
    views: list[ViewSpec] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)
    interactions: list[InteractionSpec] = field(default_factory=list)
    agents: list[dict] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    ui_artifact: dict = field(default_factory=dict)

    @classmethod
    def from_application(cls, app: Application) -> "ApplicationSpec":
        """Project an ``Application`` aggregate into its spec (sorted views/queries)."""
        return cls(
            application_id=app.id,
            workspace_id=app.workspace_id,
            name=app.name,
            description=app.description,
            version=1,
            lifecycle=app.lifecycle,
            views=[ViewSpec.from_view(app.views[key])
                   for key in sorted(app.views)],
            queries=[dict(app.queries[key].to_dict())
                     for key in sorted(app.queries)],
            interactions=[InteractionSpec.from_dict(item)
                          for item in app.interactions],
            agents=[dict(item) for item in app.agents],
            capabilities=list(app.capabilities),
            ui_artifact=dict(app.ui_artifact),
        )

    def to_dict(self) -> dict:
        return {
            "application_id": self.application_id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "lifecycle": self.lifecycle,
            "views": [view.to_dict() for view in self.views],
            "queries": [dict(query) for query in self.queries],
            "interactions": [item.to_dict() for item in self.interactions],
            "agents": [dict(agent) for agent in self.agents],
            "capabilities": list(self.capabilities),
            "ui_artifact": dict(self.ui_artifact),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ApplicationSpec":
        return cls(
            application_id=_text(data.get("application_id")),
            workspace_id=_text(data.get("workspace_id")),
            name=_text(data.get("name")),
            description=_text(data.get("description", "")),
            version=int(data.get("version", 1) or 1),
            lifecycle=_text(data.get("lifecycle", "draft")) or "draft",
            views=[ViewSpec.from_dict(item) for item in (data.get("views") or [])],
            queries=[dict(item) for item in (data.get("queries") or [])],
            interactions=[InteractionSpec.from_dict(item)
                          for item in (data.get("interactions") or [])],
            agents=[dict(item) for item in (data.get("agents") or [])],
            capabilities=[_text(item) for item in (data.get("capabilities") or [])],
            ui_artifact=_mapping(data.get("ui_artifact")),
        )

    # ------------------------------------------------------------------

    def query_aliases(self) -> set[str]:
        return {_text(query.get("alias")) for query in self.queries
                if _text(query.get("alias"))}

    def diagnostics(
        self,
        ontology: dict | None,
        capability_ids: set[str] | None = None,
    ) -> list[dict]:
        """Report dangling references as ``{code,message,path}`` dicts.

        Advisory by design: it never raises, so a console can show what would
        break before a publish (publish itself remains the hard gate).  When
        ``capability_ids`` is ``None`` the capability existence check is
        skipped (e.g. an offline spec preview without the capability store).
        """
        problems: list[dict] = []
        objects = (ontology or {}).get("objects") or {}
        if ontology is None and (self.views or self.queries):
            problems.append({
                "code": "ontology_missing",
                "message": "the workspace has no ontology revision to validate "
                           "the application against",
                "path": "",
            })
        aliases = self.query_aliases()
        for index, view in enumerate(self.views):
            path = f"views[{index}]"
            if ontology is not None and view.object_type:
                if view.object_type not in objects:
                    problems.append({
                        "code": "unknown_object_type",
                        "message": f"view {view.alias!r} references unknown "
                                   f"object type {view.object_type!r}",
                        "path": f"{path}.object_type",
                    })
            if view.query_alias and view.query_alias not in aliases:
                problems.append({
                    "code": "unknown_query",
                    "message": f"view {view.alias!r} references unknown query "
                               f"{view.query_alias!r}",
                    "path": f"{path}.query_alias",
                })
            required = _VIEW_CONFIG_REQUIRED.get(view.kind, ())
            config = view.config if isinstance(view.config, dict) else {}
            for key in required:
                value = config.get(key)
                if not isinstance(value, str) or not value.strip():
                    problems.append({
                        "code": "missing_view_config",
                        "message": f"view {view.alias!r}: {view.kind} needs "
                                   f"config.{key} naming a property",
                        "path": f"{path}.config.{key}",
                    })
        for index, interaction in enumerate(self.interactions):
            path = f"interactions[{index}]"
            if (capability_ids is not None and interaction.capability_id
                    and interaction.capability_id not in capability_ids):
                problems.append({
                    "code": "unknown_capability",
                    "message": f"interaction {interaction.id!r} references "
                               f"unknown/unpublished capability "
                               f"{interaction.capability_id!r}",
                    "path": f"{path}.capability_id",
                })
        return problems


__all__ = [
    "INTERACTION_KINDS",
    "VIEW_SURFACES",
    "ApplicationSpec",
    "InteractionSpec",
    "ViewSpec",
]
