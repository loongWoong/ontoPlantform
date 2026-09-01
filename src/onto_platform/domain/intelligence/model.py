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
        """A compact ontology + vocabulary digest for LLM prompts."""
        lines = [f"Ontology: {ontology.name} v{ontology.version}"]
        for otype in ontology.objects.values():
            props = ", ".join(f"{p.name}:{p.type}" for p in otype.properties.values())
            lines.append(f"- Object {otype.name}: {otype.description} ({props})")
        for rel in ontology.relations.values():
            lines.append(f"- Relation {rel.source} --{rel.name}--> {rel.target}")
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

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "answer": self.answer,
            "ok": self.ok,
            "steps": [s.to_dict() for s in self.steps],
        }
