"""Application use cases: build and use the end-user product (design.txt §21-§23)."""

from __future__ import annotations

from ..domain.application.model import Application, QuerySpec, View
from ..domain.application.spec import ApplicationSpec, InteractionSpec
from ..domain.governance.model import transition
from ..errors import NotFoundError, ValidationError
from .common import (
    ApplicationContext,
    active_ontology_row,
    published_ontology_row,
)

VIEW_KINDS = ("table", "card", "chart", "map")


class CreateApplication:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str,
                 description: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = Application(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name,
            description=description,
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.create", app.id,
                              {"name": name})
        return app.to_dict()


class _AppMixin:
    def _load(self, ctx: ApplicationContext, workspace_id: str, app_id: str) -> Application:
        return Application.from_dict(ctx.must_get("application", workspace_id, app_id))

    def _check_object_type(self, ctx: ApplicationContext, workspace_id: str,
                           object_type: str) -> None:
        self._object_type_spec(ctx, workspace_id, object_type)

    def _object_type_spec(self, ctx: ApplicationContext, workspace_id: str,
                          object_type: str) -> dict:
        # validate against the revision the editor is working on (a draft wins
        # over the deprecated parent), never against whatever row happens to
        # come first out of storage
        ontology = active_ontology_row(ctx, workspace_id)
        if ontology is None or object_type not in (ontology.get("objects") or {}):
            raise ValidationError(
                f"object type {object_type!r} is not defined in the ontology"
            )
        return (ontology.get("objects") or {})[object_type]

    def _assert_editable(self, app: Application) -> None:
        """A published application is the serving contract: clone it to evolve.

        Editing in place would silently change a product that users already
        run, so the only safe path is "clone into a new draft, publish again".
        """
        if app.lifecycle == "published":
            raise ValidationError(
                "published applications are immutable; clone it into a draft "
                "to edit")

    def _validate_view_config(self, view: View, properties: dict,
                              object_type: str | None = None) -> None:
        target = object_type or view.object_type
        required = {"chart": ("x", "y"), "map": ("lat", "lng")}.get(view.kind)
        if required is None:
            return
        numeric = ("y", "lat", "lng")
        for key in required:
            raw = view.config.get(key)
            if not isinstance(raw, str) or not raw.strip():
                raise ValidationError(
                    f"{view.kind} view needs config.{key} naming a property of "
                    f"{target!r}")
            prop = raw.strip()
            spec = properties.get(prop)
            if spec is None:
                raise ValidationError(
                    f"view config.{key} {prop!r} is not a property of "
                    f"{target!r}")
            if key in numeric and spec.get("type") not in ("number", "integer"):
                raise ValidationError(
                    f"view config.{key} {prop!r} must be a numeric property, "
                    f"got {spec.get('type')!r}")

    def _validate_view(self, ctx: ApplicationContext, workspace_id: str,
                       app: Application, view: View,
                       check_query_alias: bool = True) -> None:
        if view.kind not in VIEW_KINDS:
            raise ValidationError(
                f"unknown view kind {view.kind!r}; expected {list(VIEW_KINDS)}")
        # a query-driven view inherits the query's object type; chart/map still
        # need it to resolve the rendering properties
        effective_type = view.object_type
        if not effective_type and view.query_alias:
            bound = app.queries.get(view.query_alias)
            effective_type = bound.object_type if bound else ""
        if effective_type:
            spec = self._object_type_spec(ctx, workspace_id, effective_type)
            properties = spec.get("properties") or {}
            for prop in view.properties:
                if prop not in properties:
                    raise ValidationError(
                        f"view property {prop!r} is not defined on object type "
                        f"{effective_type!r}")
            self._validate_view_config(view, properties, effective_type)
        elif view.kind in ("chart", "map"):
            raise ValidationError(
                f"{view.kind} views need an object_type to map their data")
        if check_query_alias and view.query_alias \
                and view.query_alias not in app.queries:
            raise ValidationError(
                f"view query_alias {view.query_alias!r} does not name a query of "
                "this application")

    def _validate_query(self, ctx: ApplicationContext, workspace_id: str,
                        query: QuerySpec) -> None:
        if not query.object_type:
            raise ValidationError("query object_type is required")
        self._check_object_type(ctx, workspace_id, query.object_type)
        ontology = active_ontology_row(ctx, workspace_id) or {}
        relations = ontology.get("relations") or {}
        for entry in query.expand:
            relation = str(entry.get("relation") or "")
            direction = str(entry.get("direction") or "out")
            if relation not in relations:
                raise ValidationError(
                    f"query expand relation {relation!r} is not defined in the "
                    "ontology")
            if direction not in ("out", "in"):
                raise ValidationError(
                    f"query expand direction {direction!r} must be 'out' or 'in'")


class AddView(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 title: str = "", kind: str = "table", object_type: str = "",
                 properties: list[str] | None = None, query_alias: str = "",
                 config: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        view = View(
            alias=alias, title=title or alias, kind=kind,
            object_type=object_type, properties=properties or [],
            query_alias=query_alias, config=dict(config or {}),
        )
        # additions may reference a query that is created later; publish is
        # where every dangling reference is enforced
        self._validate_view(self._ctx, workspace_id, app, view,
                            check_query_alias=False)
        app.views[alias] = view
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        return app.to_dict()


class AddQuery(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 object_type: str, filters: dict | None = None,
                 expand: list[dict] | None = None, title: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        query = QuerySpec(
            alias=alias, title=title or alias, object_type=object_type,
            filters=dict(filters or {}),
            expand=QuerySpec.coerce_expand(expand),
        )
        self._validate_query(self._ctx, workspace_id, query)
        app.queries[alias] = query
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        return app.to_dict()


class UpdateApplication(_AppMixin):
    """Edit application metadata; a published app must be cloned instead."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str,
                 name: str | None = None,
                 description: str | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        if name is None and description is None:
            raise ValidationError(
                "nothing to update: pass name and/or description")
        app = self._load(self._ctx, workspace_id, app_id)
        self._assert_editable(app)
        changed: dict = {}
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValidationError("application name must not be empty")
            app.name = name
            changed["name"] = name
        if description is not None:
            app.description = str(description)
            changed["description"] = app.description
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.update", app_id,
                              changed)
        return app.to_dict()


class AttachUiArtifact(_AppMixin):
    """Attach a generated smart-UI snapshot to a draft application.

    Published applications are immutable (clone first).  The artifact is a
    self-contained published snapshot (blueprint/program plus provenance); the
    declarative views/queries are left untouched so a Studio publish can never
    silently change an existing app's data contract.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str,
                 artifact: dict) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        if not isinstance(artifact, dict) or not artifact.get("generation_id"):
            raise ValidationError("ui artifact needs a generation_id")
        app = self._load(self._ctx, workspace_id, app_id)
        self._assert_editable(app)
        app.ui_artifact = dict(artifact)
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "application.ui_artifact", app_id,
            {"generation_id": artifact.get("generation_id"),
             "draft_id": artifact.get("draft_id"),
             "version": artifact.get("version"),
             "program_hash": artifact.get("program_hash")},
        )
        return app.to_dict()


class UpdateView(_AppMixin):
    """Edit one view in place (draft/validated applications only)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 title: str | None = None, kind: str | None = None,
                 object_type: str | None = None,
                 properties: list[str] | None = None,
                 query_alias: str | None = None,
                 config: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        if all(value is None for value in
               (title, kind, object_type, properties, query_alias, config)):
            raise ValidationError("nothing to update: pass at least one view field")
        app = self._load(self._ctx, workspace_id, app_id)
        self._assert_editable(app)
        current = app.views.get(alias)
        if current is None:
            raise NotFoundError(f"view not found: {alias!r}")
        view = View(
            alias=alias,
            title=(str(title) if title is not None else current.title) or alias,
            kind=(str(kind) if kind is not None else current.kind),
            object_type=(str(object_type) if object_type is not None
                         else current.object_type),
            properties=(list(properties) if properties is not None
                        else list(current.properties)),
            query_alias=(str(query_alias) if query_alias is not None
                         else current.query_alias),
            config=(dict(config) if config is not None else dict(current.config)),
        )
        self._validate_view(self._ctx, workspace_id, app, view)
        app.views[alias] = view
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.update_view",
                              app_id, {"alias": alias,
                                       "kind": view.kind,
                                       "object_type": view.object_type})
        return app.to_dict()


class UpdateQuery(_AppMixin):
    """Edit one named query in place (draft/validated applications only)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 title: str | None = None, object_type: str | None = None,
                 filters: dict | None = None,
                 expand: list[dict] | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        if all(value is None for value in (title, object_type, filters, expand)):
            raise ValidationError(
                "nothing to update: pass at least one query field")
        app = self._load(self._ctx, workspace_id, app_id)
        self._assert_editable(app)
        current = app.queries.get(alias)
        if current is None:
            raise NotFoundError(f"query not found: {alias!r}")
        query = QuerySpec(
            alias=alias,
            title=(str(title) if title is not None else current.title) or alias,
            object_type=(str(object_type) if object_type is not None
                         else current.object_type),
            filters=(dict(filters) if filters is not None
                     else dict(current.filters)),
            expand=(QuerySpec.coerce_expand(expand) if expand is not None
                    else [dict(entry) for entry in current.expand]),
        )
        self._validate_query(self._ctx, workspace_id, query)
        app.queries[alias] = query
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.update_query",
                              app_id, {"alias": alias,
                                       "object_type": query.object_type,
                                       "expand": [dict(e) for e in query.expand]})
        return app.to_dict()


class CloneApplication(_AppMixin):
    """Copy an application (usually a published one) into a fresh draft."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str,
                 name: str | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        source = self._load(self._ctx, workspace_id, app_id)
        new_name = str(name or "").strip() or f"{source.name} 副本"
        now = self._ctx.clock()
        clone = Application(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=new_name,
            description=source.description,
            views={key: View.from_dict(view.to_dict())
                   for key, view in source.views.items()},
            queries={key: QuerySpec.from_dict(query.to_dict())
                     for key, query in source.queries.items()},
            capabilities=list(source.capabilities),
            agents=[dict(agent) for agent in source.agents],
            # interactions are declarative references (id/kind/capability), so
            # copying the dicts is enough - no live runtime state to duplicate
            interactions=[dict(item) for item in source.interactions],
            objective=dict(source.objective),
            lifecycle="draft",
            created_at=now,
            updated_at=now,
        )
        self._ctx.save("application", clone.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "application.clone", clone.id,
            {"source_id": source.id, "source_name": source.name,
             "name": new_name})
        return clone.to_dict()


class AttachCapability(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str,
                 capability_id: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        capability = self._ctx.must_get("capability", workspace_id, capability_id)
        if capability.get("lifecycle") != "published":
            raise ValidationError("only published capabilities can be attached")
        if capability_id not in app.capabilities:
            app.capabilities.append(capability_id)
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        return app.to_dict()


class AddAgent(_AppMixin):
    """Attach an agent entry point to the application (design.txt §50).

    The Agent domain lives in Intelligence; the application only stores a
    named reference with its own goal phrasing - never a second domain.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 goal: str, description: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        if not goal.strip():
            raise ValidationError("application agent needs a goal")
        app.agents = [a for a in app.agents if a.get("alias") != alias]
        app.agents.append({
            "alias": alias,
            "goal": goal.strip(),
            "description": description,
        })
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        return app.to_dict()


class AddInteraction(_AppMixin):
    """Declare an operation surface (action/workflow/form/agent) on a draft app.

    The app stores a declarative ``InteractionSpec`` only: capability execution,
    workflow state and approval enforcement stay in the runtime/security
    domains (docs/analysis/ontology-driven-application-architecture.md §3.3).
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str,
                 interaction_id: str, kind: str, title: str = "",
                 capability_id: str = "", config: dict | None = None,
                 permissions: dict | None = None,
                 approval: dict | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        self._assert_editable(app)
        for label, value in (("config", config),
                             ("permissions", permissions),
                             ("approval", approval)):
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"interaction {label} must be a dict")
        interaction = InteractionSpec(
            id=str(interaction_id or "").strip(),
            kind=str(kind or "").strip(),
            title=str(title or ""),
            capability_id=str(capability_id or "").strip(),
            config=dict(config or {}),
            permissions=dict(permissions or {}),
            approval=dict(approval or {}),
        )
        problems = interaction.validate()
        if problems:
            raise ValidationError("; ".join(problems))
        if any(str(item.get("id")) == interaction.id
               for item in app.interactions):
            raise ValidationError(
                f"interaction {interaction.id!r} already exists")
        if interaction.capability_id:
            capability = self._ctx.must_get(
                "capability", workspace_id, interaction.capability_id)
            if capability.get("lifecycle") != "published":
                raise ValidationError(
                    "only published capabilities can back an interaction")
        app.interactions.append(interaction.to_dict())
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "application.add_interaction", app_id,
            {"interaction_id": interaction.id, "kind": interaction.kind,
             "capability_id": interaction.capability_id})
        return app.to_dict()


class RunAppAgent(_AppMixin):
    """Run one of the application's agent entry points."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 goal_override: str = "", provider: "LLMProvider | None" = None,
                 max_steps: int = 8) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        app = self._load(self._ctx, workspace_id, app_id)
        agent = next((a for a in app.agents if a.get("alias") == alias), None)
        if agent is None:
            raise NotFoundError(f"agent {alias!r} not found in application {app_id!r}")
        goal = goal_override.strip() or agent["goal"]
        from .intelligence_usecases import RunAgent

        return RunAgent(self._ctx)(workspace_id, actor, goal,
                                   provider=provider, max_steps=max_steps)


class PublishApplication(_AppMixin):
    """Publish an application only when every reference is still real.

    Publishing is the promise that the app works: each view/query must resolve
    against the current **published** ontology, each attached capability must
    exist and be published, and every agent entry point needs a unique alias and
    a goal.  All problems are collected and reported at once so the console can
    show the complete list instead of a fix-one-retry loop.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def _problems(self, workspace_id: str, app: Application) -> list[str]:
        problems: list[str] = []
        # A published AI artifact is a serving surface in its own right: the
        # Studio's publish flow deliberately creates a new application with no
        # hand-built views/queries, and rejecting that made "发布" a dead end.
        if not app.views and not app.queries and not app.ui_artifact:
            problems.append("application needs at least one view or query")
        if app.ui_artifact and not (app.ui_artifact.get("generation_id")
                                    or app.ui_artifact.get("blueprint")):
            problems.append(
                "ui_artifact must reference a generation or contain a blueprint")
        ontology = published_ontology_row(self._ctx, workspace_id)
        if ontology is None:
            problems.append(
                "the workspace has no published ontology revision; "
                "publish the ontology first so the application has a serving "
                "contract to validate against")
            return problems
        objects = ontology.get("objects") or {}
        relations = ontology.get("relations") or {}
        query_aliases = set(app.queries)
        for alias, view in sorted(app.views.items()):
            where = f"view {alias!r}"
            if view.kind not in VIEW_KINDS:
                problems.append(
                    f"{where}: unknown kind {view.kind!r}; expected {list(VIEW_KINDS)}")
            effective_type = view.object_type
            if not effective_type and view.query_alias:
                bound = app.queries.get(view.query_alias)
                effective_type = bound.object_type if bound else ""
            if not effective_type:
                problems.append(f"{where}: object_type is required")
            elif effective_type not in objects:
                problems.append(
                    f"{where}: unknown object type {effective_type!r}")
            else:
                properties = (objects[effective_type].get("properties") or {})
                for prop in view.properties:
                    if prop not in properties:
                        problems.append(
                            f"{where}: unknown property {prop!r} on "
                            f"{effective_type!r}")
                if view.kind in ("chart", "map"):
                    required = {"chart": ("x", "y"),
                                "map": ("lat", "lng")}[view.kind]
                    numeric = ("y", "lat", "lng")
                    for key in required:
                        value = view.config.get(key)
                        if not isinstance(value, str) or not value.strip():
                            problems.append(
                                f"{where}: {view.kind} needs config.{key}")
                            continue
                        prop = value.strip()
                        spec = properties.get(prop)
                        if spec is None:
                            problems.append(
                                f"{where}: config.{key} {prop!r} is not a "
                                f"property of {effective_type!r}")
                        elif key in numeric and spec.get("type") not in (
                                "number", "integer"):
                            problems.append(
                                f"{where}: config.{key} {prop!r} must be a "
                                f"numeric property")
            if view.query_alias and view.query_alias not in query_aliases:
                problems.append(
                    f"{where}: query_alias {view.query_alias!r} does not name a "
                    "query of this application")
        for alias, query in sorted(app.queries.items()):
            where = f"query {alias!r}"
            if not query.object_type:
                problems.append(f"{where}: object_type is required")
            elif query.object_type not in objects:
                problems.append(
                    f"{where}: unknown object type {query.object_type!r}")
            else:
                properties = (objects[query.object_type].get("properties") or {})
                for key in query.filters:
                    prop = str(key).split("__")[0]
                    if prop not in properties:
                        problems.append(
                            f"{where}: unknown filter property {key!r} on "
                            f"{query.object_type!r}")
            for entry in query.expand:
                relation = str(entry.get("relation") or "")
                direction = str(entry.get("direction") or "out")
                if relation not in relations:
                    problems.append(
                        f"{where}: unknown relation {relation!r} in expand")
                if direction not in ("out", "in"):
                    problems.append(
                        f"{where}: expand direction {direction!r} must be "
                        "'out' or 'in'")
        capabilities = {
            row["id"]: row
            for row in self._ctx.repo.list("capability", workspace_id)
        }
        for capability_id in app.capabilities:
            row = capabilities.get(capability_id)
            if row is None:
                problems.append(
                    f"attached capability {capability_id!r} does not exist")
            elif row.get("lifecycle") != "published":
                problems.append(
                    f"attached capability {capability_id!r} is not published "
                    f"(lifecycle {row.get('lifecycle')!r})")
        seen_aliases: set[str] = set()
        for agent in app.agents:
            alias = str(agent.get("alias") or "").strip()
            goal = str(agent.get("goal") or "").strip()
            if not alias:
                problems.append("application agent needs a non-empty alias")
            elif alias in seen_aliases:
                problems.append(f"duplicate application agent alias {alias!r}")
            seen_aliases.add(alias)
            if not goal:
                problems.append(
                    f"application agent {alias or '<missing alias>'}: goal must "
                    "not be empty")
        return problems

    def __call__(self, workspace_id: str, app_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        problems = self._problems(workspace_id, app)
        if problems:
            raise ValidationError(
                "application validation failed: " + "; ".join(problems))
        if app.lifecycle == "draft":
            app.lifecycle = transition(app.lifecycle, "validate")
        app.lifecycle = transition(app.lifecycle, "publish")
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.publish", app.id,
                              {"name": app.name})
        return app.to_dict()


class RunAppQuery(_AppMixin):
    """Execute a named application query against the runtime state."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        app = self._load(self._ctx, workspace_id, app_id)
        query = app.queries.get(alias)
        if query is None:
            raise NotFoundError(f"query {alias!r} not found in application {app_id!r}")
        gateway = self._ctx.gateway_for(workspace_id)
        objects = gateway.find_objects(query.object_type, query.filters)
        expansions = []
        for spec in query.expand:
            relation = spec.get("relation", "")
            direction = spec.get("direction", "out")
            related = []
            for obj in objects:
                ref = f"{obj['type']}/{obj['id']}"
                for edge in gateway.related(ref, relation, direction):
                    target = gateway.get_object(edge)
                    if target:
                        related.append({"from": ref, "object": target})
            expansions.append({"relation": relation, "direction": direction,
                               "objects": related})
        return {"query": alias, "objects": objects, "expand": expansions}


class GetApplication(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.must_get("application", workspace_id, app_id)


class GetApplicationSpec(_AppMixin):
    """Read the application as one ApplicationSpec plus advisory diagnostics.

    This is the "what would this app be" view for consoles/CI: dangling
    object types, queries and capabilities are reported as data instead of
    raising, so an editor can show every problem at once.  Publish remains the
    hard gate; this use case never mutates and never fails on bad references.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        app = self._load(self._ctx, workspace_id, app_id)
        spec = ApplicationSpec.from_application(app)
        ontology = active_ontology_row(self._ctx, workspace_id)
        # Judge capabilities against the *published* set: an interaction can
        # only execute a published capability (AddInteraction enforces it), so
        # a capability that was later unpublished shows up as a diagnostic.
        capability_ids = {
            str(row["id"])
            for row in self._ctx.repo.list("capability", workspace_id)
            if row.get("lifecycle") == "published" and row.get("id")
        }
        diagnostics = spec.diagnostics(ontology, capability_ids)
        return {"spec": spec.to_dict(), "diagnostics": diagnostics,
                "valid": not diagnostics}


class DeleteApplication:
    """Delete the application aggregate; runtime state and audit stay intact."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = Application.from_dict(
            self._ctx.must_get("application", workspace_id, app_id))
        with self._ctx.transaction():
            self._ctx.repo.delete("application", workspace_id, app_id)
        self._ctx.audit_event(
            actor, workspace_id, "application.delete", app_id,
            {"name": app.name, "views": sorted(app.views),
             "queries": sorted(app.queries), "agents": len(app.agents),
             "capabilities": len(app.capabilities),
             "lifecycle": app.lifecycle})
        return {
            "deleted": True,
            "id": app_id,
            "name": app.name,
            "removed_views": len(app.views),
            "removed_queries": len(app.queries),
            "removed_agents": len(app.agents),
        }


class RemoveView(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, alias: str,
                 actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        if alias not in app.views:
            raise NotFoundError(f"view not found: {alias!r}")
        del app.views[alias]
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.remove_view",
                              app_id, {"alias": alias})
        return app.to_dict()


class RemoveQuery(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, alias: str,
                 actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        if alias not in app.queries:
            raise NotFoundError(f"query not found: {alias!r}")
        views = [name for name, view in app.views.items()
                 if view.query_alias == alias]
        if views:
            raise ValidationError(
                f"query {alias!r} is used by views {views}; delete them first")
        del app.queries[alias]
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.remove_query",
                              app_id, {"alias": alias})
        return app.to_dict()


class RemoveAgent(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, alias: str,
                 actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        index = next((i for i, item in enumerate(app.agents)
                      if item.get("alias") == alias), None)
        if index is None:
            raise NotFoundError(f"agent not found: {alias!r}")
        app.agents.pop(index)
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id, "application.remove_agent",
                              app_id, {"alias": alias})
        return app.to_dict()


class RemoveInteraction(_AppMixin):
    """Remove one declared operation surface from a draft application."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, interaction_id: str,
                 actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        self._assert_editable(app)
        index = next((i for i, item in enumerate(app.interactions)
                      if str(item.get("id")) == interaction_id), None)
        if index is None:
            raise NotFoundError(
                f"interaction not found: {interaction_id!r}")
        app.interactions.pop(index)
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(
            actor, workspace_id, "application.remove_interaction", app_id,
            {"interaction_id": interaction_id})
        return app.to_dict()


class DetachCapability(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, capability_id: str,
                 actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        if capability_id not in app.capabilities:
            raise NotFoundError(
                f"capability {capability_id!r} is not attached to the application")
        app.capabilities = [item for item in app.capabilities
                            if item != capability_id]
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        self._ctx.audit_event(actor, workspace_id,
                              "application.detach_capability", app_id,
                              {"capability_id": capability_id})
        return app.to_dict()


class ListApplications:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("application", workspace_id)
