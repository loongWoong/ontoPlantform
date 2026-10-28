"""Intelligence: AI 是横向能力，不是第五个并列业务模块 (design.txt §24)。

    Semantic  连接 Ontology 与 LLM (术语 -> 概念, schema -> context)
    LLM       理解 / 结构化生成 / 推理 (通过 LLMProvider Port)
    Agent     Goal -> Understand -> Plan -> Act -> Observe -> Answer

Agent 不直接操作 Kernel：Agent -> Capability -> RuntimeGateway -> Kernel，
基础本体观察 (describe/find/related/get) 除外 (design.txt §28)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ontology.model import Ontology


@dataclass(slots=True)
class SemanticIndex:
    """Vocabulary-backed bridge between business language and ontology."""

    entries: list[dict] = field(default_factory=list)  # VocabularyTerm dicts

    @classmethod
    def from_ontology(cls, ontology: Ontology) -> "SemanticIndex":
        return cls(entries=[t.to_dict() for t in ontology.vocabulary.values()])

    def resolve(self, text: str) -> list[dict]:
        """Terms mentioned in ``text`` (longest term first)."""
        hits: list[dict] = []
        for entry in sorted(self.entries, key=lambda e: len(e["term"]), reverse=True):
            needles = [entry["term"], *entry.get("aliases", [])]
            if any(n and n in text for n in needles):
                hits.append(entry)
        return hits

    def context_text(self, ontology: Ontology) -> str:
        """A compact ontology + vocabulary digest for LLM prompts.

        O-11: facets are machine-readable - enum values, units, identity,
        category and temporal mode all appear inline so the model can reason
        about valid values instead of guessing them.
        """
        lines = [f"Ontology: {ontology.name} v{ontology.version}"]
        for otype in ontology.objects.values():
            facets: list[str] = []
            if otype.identity:
                facets.append("identified by " + ", ".join(otype.identity))
            if otype.category and otype.category != "object":
                facets.append(f"category: {otype.category}")
            lines.append(
                f"- Object {otype.name}: {otype.description} "
                f"({'; '.join(facets)})" if facets else
                f"- Object {otype.name}: {otype.description}")
            for prop in otype.properties.values():
                details = [f"type: {prop.type}"]
                if prop.required:
                    details.append("required")
                if prop.enum:
                    details.append(f"{otype.name}.{prop.name} ∈ "
                                   "{" + ", ".join(str(v) for v in prop.enum) + "}")
                if prop.unit:
                    details.append(f"unit: {prop.unit}")
                if prop.temporal:
                    details.append(f"temporal: {prop.temporal}")
                lines.append(f"  - {prop.name} ({', '.join(details)})")
        for rel in ontology.relations.values():
            lines.append(f"- Relation {rel.source} --{rel.name}--> {rel.target}"
                         + (f" (cardinality: {rel.cardinality})"
                            if rel.cardinality else ""))
        if self.entries:
            lines.append("Business vocabulary:")
            for entry in self.entries:
                lines.append(f"- {entry['term']} -> {entry['concept']} ({entry['kind']})")
        return "\n".join(lines)


@dataclass(slots=True)
class AgentStep:
    """One Understand -> Act -> Observe round."""

    index: int
    thought: str
    action: dict
    observation: str

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "thought": self.thought,
            "action": self.action,
            "observation": self.observation,
        }


@dataclass(slots=True)
class AgentRun:
    goal: str
    answer: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    ok: bool = True
    #: how the loop ended: answered | max_steps | provider_error | invalid_json
    termination: str = "answered"
    #: a natural-language answer is not proof.  The platform only marks a run
    #: verified when a machine-checkable acceptance criterion passed.
    verified: bool = False
    verification: dict = field(default_factory=dict)
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    #: provider-reported token usage when the provider exposes it, otherwise
    #: null (never an estimated number presented as measured)
    usage: dict | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "answer": self.answer,
            "ok": self.ok,
            "termination": self.termination,
            "verified": self.verified,
            "verification": self.verification,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "usage": self.usage,
            "steps": [s.to_dict() for s in self.steps],
        }
