"""Ontology use cases + the shared SchemaPublisher.

SchemaPublisher is the single place where Design Time becomes Run Time
(design.txt §9/§20): it compiles the workspace's published ontology plus
all published capabilities and hands the payload to the RuntimeGateway.
Both PublishOntology and PublishCapability go through it, so the kernel
schema is always the exact composition of published platform assets.
"""

from __future__ import annotations

from ..domain.governance.model import transition
from ..domain.ontology.compiler import OntologyCompiler
from ..domain.ontology.model import (
    Ontology,
    ObjectTypeSpec,
    PropertySpec,
    RelationSpec,
    VocabularyTerm,
)
from ..domain.ontology.validation import validate_ontology
from ..errors import NotFoundError, ValidationError
from .common import ApplicationContext


def _load_workspace_ontology(ctx: ApplicationContext, workspace_id: str,
                             required: bool = True) -> Ontology | None:
    """v0.1: one ontology per workspace - it IS the workspace's world."""
    items = ctx.repo.list("ontology", workspace_id)
    if not items:
        if required:
            raise NotFoundError(
                f"workspace {workspace_id!r} has no ontology yet; create one first"
            )
        return None
    return Ontology.from_dict(items[0])


class CreateOntology:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, name: str, actor: str,
                 description: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        existing = self._ctx.repo.list("ontology", workspace_id)
        if existing:
            raise ValidationError(
                "this workspace already has an ontology; v0.1 supports one "
                "ontology per workspace"
            )
        ontology = Ontology(
            id=self._ctx.id_factory(),
            workspace_id=workspace_id,
            name=name.strip() or "untitled",
            description=description,
            created_at=self._ctx.clock(),
            updated_at=self._ctx.clock(),
        )
        self._ctx.save("ontology", ontology.to_dict())
        self._ctx.audit_event(actor, workspace_id, "ontology.create", ontology.id,
                              {"name": ontology.name})
        return ontology.to_dict()


class GetOntology:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        return ontology.to_dict()


class UpsertObjectType:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str,
                 properties: list[dict] | dict, description: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        props = (
            [PropertySpec.from_dict(p) for p in properties.values()]
            if isinstance(properties, dict)
            else [PropertySpec.from_dict(p) for p in properties]
        )
        spec = ObjectTypeSpec(
            name=name,
            description=description,
            properties={p.name: p for p in props},
        )
        ontology.upsert_object(spec)
        ontology.updated_at = self._ctx.clock()
        self._ctx.save("ontology", ontology.to_dict())
        self._ctx.audit_event(actor, workspace_id, "ontology.upsert_object", name,
                              {"properties": sorted(spec.properties)})
        return ontology.to_dict()


class UpsertRelation:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, name: str, source: str,
                 target: str, description: str = "", cardinality: str = "") -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        ontology.upsert_relation(
            RelationSpec(name=name, source=source, target=target,
                         description=description, cardinality=cardinality)
        )
        ontology.updated_at = self._ctx.clock()
        self._ctx.save("ontology", ontology.to_dict())
        self._ctx.audit_event(actor, workspace_id, "ontology.upsert_relation", name,
                              {"source": source, "target": target})
        return ontology.to_dict()


class AddVocabularyTerm:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, term: str, concept: str,
                 kind: str = "object", aliases: list[str] | None = None) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        ontology.add_term(
            VocabularyTerm(term=term, concept=concept, kind=kind, aliases=aliases or [])
        )
        ontology.updated_at = self._ctx.clock()
        self._ctx.save("ontology", ontology.to_dict())
        self._ctx.audit_event(actor, workspace_id, "ontology.add_term", term,
                              {"concept": concept})
        return ontology.to_dict()


class ValidateOntology:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> list[str]:
        self._ctx.require(actor, workspace_id, "viewer")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        return validate_ontology(ontology)


class PublishOntology:
    def __init__(self, ctx: ApplicationContext, publisher: "SchemaPublisher") -> None:
        self._ctx = ctx
        self._publisher = publisher

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "editor")
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        problems = validate_ontology(ontology)
        if problems:
            raise ValidationError(
                "ontology validation failed: " + "; ".join(problems)
            )
        ontology.lifecycle = transition(ontology.lifecycle, "validate")
        ontology.lifecycle = transition(ontology.lifecycle, "publish")
        ontology.updated_at = self._ctx.clock()
        self._ctx.save("ontology", ontology.to_dict())
        describe = self._publisher.publish(workspace_id, actor, action="ontology.publish")
        return {"ontology": ontology.to_dict(), "runtime": describe}


class SchemaPublisher:
    """Compile published platform assets -> kernel schema (one door)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def publish(self, workspace_id: str, actor: str, action: str) -> dict:
        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        if ontology.lifecycle != "published":
            raise ValidationError(
                "the workspace ontology is not published; publish it first"
            )
        capabilities = [
            c
            for c in self._ctx.repo.list("capability", workspace_id)
            if c.get("lifecycle") == "published"
        ]
        from ..domain.capability.model import Capability

        payload = OntologyCompiler().compile(
            ontology, [Capability.from_dict(c) for c in capabilities]
        )
        workspace = self._ctx.repo.get("workspace", None, workspace_id)
        runtime_config = (workspace or {}).get("runtime_config", {})
        gateway = self._ctx.gateway_for(workspace_id)
        # publish first; only a successfully compiled+accepted payload is
        # persisted for cross-process self-healing (see gateway_for)
        gateway.publish_schema(payload)
        self._register_agent_runners(workspace_id, gateway, capabilities)
        # persist the compiled payload so a new process can rebuild the
        # runtime without a re-publish (see ApplicationContext.gateway_for)
        self._ctx.save("schema_payload", {
            "id": workspace_id,
            "workspace_id": workspace_id,
            "schema": payload.schema,
            "bindings": payload.bindings,
            "workflows": payload.workflows,
        })
        self._ctx.audit_event(
            actor, workspace_id, action, ontology.id,
            {
                "objects": sorted(ontology.objects),
                "relations": sorted(ontology.relations),
                "functions": sorted(payload.schema.get("functions", {})),
            },
        )
        return gateway.describe()

    def _register_agent_runners(self, workspace_id: str, gateway,
                                capabilities: list[dict]) -> None:
        """Agent-kind capabilities need a live runner (goal + LLM provider);
        it cannot live in the compiled payload, so it is attached here."""
        from .agent_runner import make_agent_runner

        from ..domain.intelligence.model import SemanticIndex

        ontology = _load_workspace_ontology(self._ctx, workspace_id)
        assert ontology is not None
        context_text = SemanticIndex.from_ontology(ontology).context_text(ontology)
        for data in capabilities:
            impl = data.get("implementation", {})
            if impl.get("kind") != "agent":
                continue
            function_name = f"{data['target_type']}.{data['key']}"
            gateway.register_agent_runner(
                f"agent://{data['id']}",
                make_agent_runner(
                    gateway,
                    lambda: self._ctx.llm,
                    context_text,
                    impl.get("goal", ""),
                ),
            )


class DescribeRuntime:
    """Runtime introspection for UIs and agents (index level)."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return self._ctx.gateway_for(workspace_id).describe()
