"""Intelligence use cases: AskOntology (deterministic) + RunAgent (LLM loop)."""

from __future__ import annotations

from ..domain.intelligence.model import SemanticIndex
from ..errors import ValidationError
from ..ports.llm import LLMProvider
from .agent_runner import AgentRunner
from .common import ApplicationContext


def _ontology_of(ctx: ApplicationContext, workspace_id: str):
    items = ctx.repo.list("ontology", workspace_id)
    if not items:
        raise ValidationError(f"workspace {workspace_id!r} has no ontology")
    return items[0]


class AskOntology:
    """Deterministic natural-language query over the business world.

    Semantic Layer (design.txt §26): 用户语言 -> 本体概念 -> Runtime State。
    v0.1 resolves vocabulary terms, locates mentioned objects (values
    appearing in the question) and expands one hop of relations - no LLM
    required, fully auditable.
    """

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, question: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        ontology_data = _ontology_of(self._ctx, workspace_id)
        ontology = _ontology_from_dict(ontology_data)
        index = SemanticIndex.from_ontology(ontology)
        gateway = self._ctx.gateway_for(workspace_id)

        hits = index.resolve(question)
        object_types: list[str] = []
        property_hits: list[dict] = []
        for hit in hits:
            if hit["kind"] == "object":
                if hit["concept"] not in object_types:
                    object_types.append(hit["concept"])
            elif hit["kind"] == "property":
                property_hits.append(hit)
                owner = hit["concept"].split(".")[0]
                if owner not in object_types:
                    object_types.append(owner)
            elif hit["kind"] == "relation":
                rdef = ontology.relations.get(hit["concept"])
                if rdef and rdef.source not in object_types:
                    object_types.append(rdef.source)

        found: list[dict] = []
        for object_type in object_types:
            try:
                candidates = gateway.find_objects(object_type)
            except Exception:
                continue  # type not published yet
            mentioned = [
                obj
                for obj in candidates
                if _mentions(obj, question)
            ]
            found.extend(mentioned or candidates[:10])

        related_objects: list[dict] = []
        for obj in found[:10]:
            ref = f"{obj['type']}/{obj['id']}"
            for relation in ontology.relations.values():
                if relation.source == obj["type"]:
                    direction = "out"
                elif relation.target == obj["type"]:
                    direction = "in"
                else:
                    continue
                for edge in gateway.related(ref, relation.name, direction):
                    target = gateway.get_object(edge)
                    if target:
                        related_objects.append(
                            {"from": ref, "relation": relation.name, "object": target}
                        )

        answer = _compose_answer(question, hits, found, related_objects)
        self._ctx.audit_event(actor, workspace_id, "intelligence.ask", "ask",
                              {"question": question, "concepts": [h["term"] for h in hits]})
        return {
            "question": question,
            "answer": answer,
            "concepts": hits,
            "objects": found,
            "related": related_objects,
        }


class RunAgent:
    """LLM-driven agent loop; provider comes from the platform config."""

    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def __call__(self, workspace_id: str, actor: str, goal: str,
                 provider: LLMProvider | None = None,
                 max_steps: int = 8) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        effective = provider or self._ctx.llm
        if effective is None:
            raise ValidationError(
                "no LLM provider configured; pass one or configure the platform"
            )
        ontology_data = _ontology_of(self._ctx, workspace_id)
        ontology = _ontology_from_dict(ontology_data)
        context_text = SemanticIndex.from_ontology(ontology).context_text(ontology)
        gateway = self._ctx.gateway_for(workspace_id)
        run = AgentRunner(gateway, effective, context_text, max_steps=max_steps).run(goal)
        self._ctx.audit_event(actor, workspace_id, "intelligence.agent", "agent",
                              {"goal": goal, "ok": run.ok, "steps": len(run.steps)})
        return run.to_dict()


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _ontology_from_dict(data: dict):
    from ..domain.ontology.model import Ontology

    return Ontology.from_dict(data)


def _mentions(obj: dict, question: str) -> bool:
    """True when any string property value of the object appears in the question."""
    for value in obj.get("properties", {}).values():
        if isinstance(value, str) and len(value) >= 2 and value in question:
            return True
    return False


def _compose_answer(question: str, hits: list[dict], objects: list[dict],
                    related: list[dict]) -> str:
    parts: list[str] = []
    if hits:
        terms = "、".join(h["term"] for h in hits)
        concepts = "、".join(sorted({h["concept"] for h in hits}))
        parts.append(f"识别业务术语：{terms} → {concepts}")
    if objects:
        lines = []
        for obj in objects[:10]:
            props = ", ".join(f"{k}={v}" for k, v in list(obj["properties"].items())[:6])
            lines.append(f"- {obj['type']}/{obj['id']}: {props}")
        parts.append("运行时状态：\n" + "\n".join(lines))
    else:
        parts.append("运行时状态中未找到匹配对象。")
    if related:
        lines = [
            f"- {r['from']} --{r['relation']}--> "
            f"{r['object']['type']}/{r['object']['id']}"
            for r in related[:10]
        ]
        parts.append("关联对象：\n" + "\n".join(lines))
    return "\n".join(parts)
