"""Generator plugins for Smart-UI.

A generator only proposes.  It never writes the generation row, never compiles
and never publishes: the pipeline keeps those core responsibilities so a
third-party generator cannot bypass adjudication or the deterministic
compiler.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ...ports.smart_ui import Candidate
from .bridge import compile_smart_ui
from .prompt import DefaultPromptPack, PromptRequest


class ModelUnavailable(RuntimeError):
    """The provider is missing or a call failed at transport level."""


class PromptTooLarge(RuntimeError):
    """The rendered prompt exceeds the generation budget."""

    def __init__(self, prompt_bytes: int) -> None:
        super().__init__(f"上下文过大（{prompt_bytes} 字节）")
        self.prompt_bytes = prompt_bytes


class InvalidProposal(ValueError):
    """The model answered but no JSON object could be extracted."""

    def __init__(self, message: str, raw_preview: str = "",
                 trace: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.raw_preview = raw_preview
        self.trace = dict(trace or {})


@dataclass
class GenerationRequest:
    """Everything a generator may read; nothing a generator may mutate."""

    platform: Any
    workspace_id: str
    actor: str
    question: str
    roles: list[str]
    focus: dict[str, Any] | None = None
    base_blueprint: dict[str, Any] | None = None
    instruction: str = ""
    mode: str = "auto"
    pack: dict[str, Any] = field(default_factory=dict)
    rule_pack: dict[str, Any] | None = None
    snapshot: Any = None
    provider: Any = None
    max_prompt_bytes: int = 0


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start:end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DeterministicGenerator:
    """L3 keyword planner + L4 compile: the safety net, never a blank page."""

    id = "onto.smartui.generator.deterministic"
    version = "1.0.0"

    def generate(self, request: GenerationRequest) -> Candidate:
        result = compile_smart_ui(
            request.platform, request.workspace_id, request.actor,
            request.question, request.roles, focus=request.focus,
            rule_pack=request.rule_pack)
        compiled = result["compiled"]
        blueprint = compiled.get("blueprint")
        if not blueprint:
            from ...errors import ValidationError
            raise ValidationError("deterministic fallback produced no blueprint")
        artifacts = compiled.get("artifacts") or {}
        return Candidate(
            source=self.id,
            blueprint=blueprint,
            compiled=compiled,
            trace={
                "generatorPlugin": self.id,
                "rulePack": artifacts.get("rulePack") or {},
                "ruleTrace": artifacts.get("ruleTrace") or {},
                "clarify": artifacts.get("clarify") or [],
            },
        )


class RuleGenerator:
    """No-model generator: evaluate the declarative rule pack, then compile.

    It never calls an LLM and never bypasses L4: the rule pack only decides the
    goal/object/focus/view candidates, and the unchanged compiler remains the
    authority for the emitted program.
    """

    id = "onto.smartui.generator.rule"
    version = "1.0.0"

    def generate(self, request: GenerationRequest) -> Candidate:
        result = compile_smart_ui(
            request.platform, request.workspace_id, request.actor,
            request.question, request.roles, focus=request.focus,
            rule_pack=request.rule_pack)
        compiled = result["compiled"]
        blueprint = compiled.get("blueprint")
        if not blueprint:
            from ...errors import ValidationError
            raise ValidationError("rule generation produced no blueprint")
        artifacts = compiled.get("artifacts") or {}
        rule_trace = artifacts.get("ruleTrace") or {}
        rule_pack = artifacts.get("rulePack") or {}
        return Candidate(
            source=self.id,
            blueprint=blueprint,
            compiled=compiled,
            trace={
                "generatorPlugin": self.id,
                "rulePack": rule_pack,
                "rulePackId": rule_pack.get("id"),
                "rulePackHash": (rule_trace.get("pack") or {}).get("hash"),
                "ruleTrace": rule_trace,
                "clarify": artifacts.get("clarify") or [],
            },
        )


class LLMGenerator:
    """Prompt -> provider -> JSON proposal.

    The retry policy is part of the generator contract because it concerns
    model output, not adjudication.  Compilation still happens in the core
    pipeline after the adjudicator chain.
    """

    id = "onto.smartui.generator.llm"
    version = "1.0.0"

    def _prompt_pack(self, request: GenerationRequest):
        host = getattr(request.platform, "smartui", None)
        workspace_pack = (request.pack or {}).get("workspacePack") or {}
        prompt_id = str(workspace_pack.get("promptPack")
                        or "onto.smartui.prompt.default")
        if host is not None:
            try:
                return host.implementation(
                    prompt_id,
                    permission="prompt.render",
                )
            except Exception:
                if prompt_id != "onto.smartui.prompt.default":
                    # A pack must not silently fall back to a different prompt
                    # contract; the caller sees the bind error.
                    raise
                pass
        return DefaultPromptPack()

    def generate(self, request: GenerationRequest) -> Candidate:
        provider = request.provider
        if provider is None:
            provider = request.platform.ctx.llm_for(request.workspace_id)
        if provider is None:
            raise ModelUnavailable("当前工作空间未配置模型")
        model_name = str(getattr(provider, "model", "") or type(provider).__name__)

        system, user = self._prompt_pack(request).render(PromptRequest(
            pack=request.pack,
            question=request.question,
            base_blueprint=request.base_blueprint,
            instruction=request.instruction,
        ))
        prompt_text = system + "\n" + user
        prompt_bytes = len(prompt_text.encode("utf-8"))
        if request.max_prompt_bytes and prompt_bytes > request.max_prompt_bytes:
            raise PromptTooLarge(prompt_bytes)

        from ...ports.llm import LLMMessage

        trace: dict[str, Any] = {
            "generatorPlugin": self.id,
            "model": model_name,
            "provider": type(provider).__name__,
            "promptHash": _sha(prompt_text),
            "promptBytes": prompt_bytes,
        }
        proposal: dict[str, Any] | None = None
        last_error = ""
        raw_preview = ""
        for attempt in range(2):
            messages = [LLMMessage("system", system), LLMMessage("user", user)]
            if attempt == 1:
                messages.append(LLMMessage(
                    "user", "上一次输出不是合法 JSON。只输出一个 JSON 对象，不要代码块。"))
            try:
                response = provider.complete(messages)
            except Exception as exc:  # transport failure -> deterministic fallback
                reason = str(exc) or type(exc).__name__
                raise ModelUnavailable(reason) from exc
            content = getattr(response, "content", "") or ""
            usage = getattr(response, "usage", None)
            if isinstance(usage, dict):
                trace["usage"] = dict(usage)
            trace["responseBytes"] = len(content.encode("utf-8"))
            proposal = _extract_json(content)
            if proposal is not None:
                break
            last_error = "模型输出不是合法 JSON"
            if content:
                raw_preview = content[:2000]
        if proposal is None:
            raise InvalidProposal(f"{last_error}（已重试 1 次）", raw_preview, trace)
        return Candidate(source=self.id, proposal=proposal, trace=trace)
