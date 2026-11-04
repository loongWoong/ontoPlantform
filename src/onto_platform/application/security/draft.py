"""AI-assisted policy drafting: propose, validate, never persist.

The model is a *proposal* source.  A draft returned by this service has no
policy id, no status and no storage write; an administrator must POST the
returned document to the normal create → validate → approve → publish
lifecycle.  This keeps the PAP authoritative and makes prompt injection from a
document unable to publish itself.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from ...errors import ValidationError
from ...ports.llm import LLMMessage
from .policy import (
    default_policy_document,
    policy_validation_report,
)

MAX_PROMPT = 4000
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _extract_json(content: str) -> dict | None:
    text = (content or "").strip()
    match = _FENCE.search(text)
    if match:
        text = match.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _base_document(ctx: Any, workspace_id: str) -> dict:
    try:
        from .service import POLICY_KIND, PolicyRecord

        for row in ctx.repo.list(POLICY_KIND, workspace_id):
            record = PolicyRecord.from_dict(row)
            if record.status == "published":
                return deepcopy(record.document)
    except Exception:
        pass
    return default_policy_document()


class PolicyDraftService:
    """Draft a policy document from natural language (design §5.2 / V0.4)."""

    def __init__(self, ctx: Any, security: Any) -> None:
        self.ctx = ctx
        self.security = security

    def draft(self, workspace_id: str, actor: str, prompt: str, *,
              mode: str = "auto") -> dict:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValidationError("prompt must not be empty")
        if len(prompt) > MAX_PROMPT:
            raise ValidationError(f"prompt must be ≤ {MAX_PROMPT} characters")
        # A draft is a policy change: only someone allowed to publish policies
        # may generate one.  (The draft itself is not stored/published.)
        if self.security is not None:
            self.security.check_action(actor, workspace_id, "security.publish")
        base = _base_document(self.ctx, workspace_id)
        text = ""
        source = "deterministic"
        if mode != "deterministic":
            provider = None
            try:
                provider = self.ctx.llm_for(workspace_id)
            except Exception:
                provider = getattr(self.ctx, "llm", None)
            if provider is None and mode == "llm":
                raise ValidationError(
                    "mode 'llm' requires a configured model provider")
            if provider is not None:
                system = (
                    "You are a policy author for an ontology platform. Return "
                    "ONLY one JSON object matching this schema: roles, "
                    "actionPolicies, scopes, fieldPolicies, approval, metadata. "
                    "Never invent roles outside the built-in set. The document "
                    "is a proposal and will be validated by the platform.")
                user = (
                    f"Current policy:\n{json.dumps(base, ensure_ascii=False)}\n"
                    f"Request:\n{prompt}")
                try:
                    response = provider.complete([
                        LLMMessage(role="system", content=system),
                        LLMMessage(role="user", content=user),
                    ])
                    text = getattr(response, "content", "") or ""
                    source = "llm"
                except Exception as exc:  # model failure falls back safely
                    text = ""
                    source = f"deterministic_after_{type(exc).__name__}"
        document = _extract_json(text) if text else None
        if document is None:
            document = base
            if mode == "llm" and source == "llm":
                raise ValidationError("model did not return a JSON policy object")
            source = "deterministic" if source.startswith("llm") else source
        report = policy_validation_report(document)
        return {
            "document": document,
            "validation": report,
            "source": source,
            "stored": False,
            "published": False,
            "next": ["POST /security/policies", "POST .../validate",
                     "POST .../approve", "POST .../publish"],
        }
