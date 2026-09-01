"""Application use cases: build and use the end-user product (design.txt §21-§23)."""

from __future__ import annotations

from ..domain.application.model import Application, QuerySpec, View
from ..domain.governance.model import transition
from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext

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
        ontologies = ctx.repo.list("ontology", workspace_id)
        if not ontologies or object_type not in ontologies[0].get("objects", {}):
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
            filters=dict(filters or {}), expand=list(expand or []),
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
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, app_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        app = self._load(self._ctx, workspace_id, app_id)
        if not app.views and not app.queries:
            raise ValidationError("application needs at least one view or query")
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


class ListApplications:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[dict]:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.repo.list("application", workspace_id)
