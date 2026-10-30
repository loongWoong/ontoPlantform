"""Application use cases: build and use the end-user product (design.txt §21-§23)."""

from __future__ import annotations

from ..domain.application.model import Application, QuerySpec, View
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
        # validate against the revision the editor is working on (a draft wins
        # over the deprecated parent), never against whatever row happens to
        # come first out of storage
        ontology = active_ontology_row(ctx, workspace_id)
        if ontology is None or object_type not in ontology.get("objects", {}):
            raise ValidationError(
                f"object type {object_type!r} is not defined in the ontology"
            )


class AddView(_AppMixin):
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str, alias: str,
                 title: str = "", kind: str = "table", object_type: str = "",
                 properties: list[str] | None = None, query_alias: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        if kind not in VIEW_KINDS:
            raise ValidationError(f"unknown view kind {kind!r}; expected {list(VIEW_KINDS)}")
        if object_type:
            self._check_object_type(self._ctx, workspace_id, object_type)
        app.views[alias] = View(
            alias=alias, title=title or alias, kind=kind,
            object_type=object_type, properties=properties or [],
            query_alias=query_alias,
        )
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
        self._check_object_type(self._ctx, workspace_id, object_type)
        app.queries[alias] = QuerySpec(
            alias=alias, title=title or alias, object_type=object_type,
            filters=dict(filters or {}),
            expand=QuerySpec.coerce_expand(expand),
        )
        app.updated_at = self._ctx.clock()
        self._ctx.save("application", app.to_dict())
        return app.to_dict()


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
        if not app.views and not app.queries:
            problems.append("application needs at least one view or query")
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
            if not view.object_type:
                problems.append(f"{where}: object_type is required")
            elif view.object_type not in objects:
                problems.append(
                    f"{where}: unknown object type {view.object_type!r}")
            else:
                properties = (objects[view.object_type].get("properties") or {})
                for prop in view.properties:
                    if prop not in properties:
                        problems.append(
                            f"{where}: unknown property {prop!r} on "
                            f"{view.object_type!r}")
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
