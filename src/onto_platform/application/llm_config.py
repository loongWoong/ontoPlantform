"""Per-workspace model configuration (P1 identity/model tier).

The API key is write-only: PUT stores an encrypted envelope, GET returns a
mask only.  Changing the model is an audited, workspace-scoped action so the
console can show who changed what, without ever echoing the secret back.
"""

from __future__ import annotations

from ..errors import ValidationError
from ..infrastructure.security import mask, protect
from .common import ApplicationContext

_ALLOWED = {"provider", "base_url", "model", "api_key"}


def _view(row: dict | None) -> dict:
    row = row or {}
    key = row.get("api_key") or ""
    return {
        "provider": row.get("provider") or "none",
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "configured": bool(key),
        "api_key_masked": row.get("api_key_masked") or "",
        "has_key": bool(key),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
    }


class LlmConfig:
    def __init__(self, ctx: ApplicationContext) -> None:
        self._ctx = ctx

    def get(self, workspace_id: str, actor: str) -> dict:
        self._ctx.require(actor, workspace_id, "viewer")
        return _view(self._ctx.repo.get("llm_config", workspace_id, "llm"))

    def put(self, workspace_id: str, actor: str, body: dict) -> dict:
        from .events import EventLog

        self._ctx.require_action(actor, workspace_id, "settings.manage")
        unknown = sorted(set(body) - _ALLOWED)
        if unknown:
            raise ValidationError(
                f"unknown model config fields: {unknown}; allowed are "
                f"{sorted(_ALLOWED)}")
        previous = self._ctx.repo.get("llm_config", workspace_id, "llm") or {}
        provider = str(body.get("provider", previous.get("provider") or
                                "openai")).strip()
        if provider not in ("openai", "none"):
            raise ValidationError(
                f"unsupported provider {provider!r}; use 'openai' or 'none'")
        row = {
            "id": "llm",
            "workspace_id": workspace_id,
            "provider": provider,
            "base_url": str(body.get("base_url", previous.get("base_url")
                                    or "https://api.openai.com/v1")),
            "model": str(body.get("model", previous.get("model")
                                  or "gpt-4o-mini")),
            "api_key": previous.get("api_key") or "",
            "updated_at": self._ctx.clock(),
            "updated_by": actor,
        }
        if "api_key" in body:
            raw = str(body.get("api_key") or "")
            if raw:
                row["api_key"] = protect(raw)
                # the mask is derived only for display; plaintext is never
                # persisted or returned
                row["api_key_masked"] = mask(raw)
        if provider == "none":
            row["api_key"] = ""
        with self._ctx.transaction():
            self._ctx.save("llm_config", row)
            self._ctx.audit_event(
                actor, workspace_id, "llm.configured", "llm",
                {"provider": row["provider"], "model": row["model"],
                 "base_url": row["base_url"],
                 "key_changed": "api_key" in body},
            )
            EventLog(self._ctx).emit(
                "settings.changed", workspace_id, actor, "settings", "llm",
                payload={"provider": row["provider"], "model": row["model"],
                         "key_changed": "api_key" in body})
        view = _view(row)
        view["api_key_masked"] = row.get("api_key_masked") or ""
        return view

    def validate(self, workspace_id: str, actor: str) -> dict:
        """Read back the effective provider without calling the model."""
        self._ctx.require(actor, workspace_id, "viewer")
        provider = self._ctx.llm_for(workspace_id)
        return {"ok": provider is not None,
                "provider": type(provider).__name__ if provider else "none",
                "configured": provider is not None}
