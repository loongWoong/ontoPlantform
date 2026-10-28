"""LLM provider factory: environment-driven configuration (no code change).

The platform default provider can be pinned from the environment so that a
deployment does not need to touch code or a settings file:

    ONTO_LLM_BASE_URL   OpenAI-compatible endpoint (implies provider on)
    ONTO_LLM_MODEL      model name          (default gpt-4o-mini)
    ONTO_LLM_API_KEY    bearer key          (also accepts ONTO_LLM_KEY)
"""

from __future__ import annotations

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


def provider_from_env():
    """Build an OpenAI-compatible provider from ``ONTO_LLM_*``, or None."""
    import os

    from .openai_compat import OpenAICompatProvider

    base_url = os.environ.get("ONTO_LLM_BASE_URL", "").strip()
    api_key = (
        os.environ.get("ONTO_LLM_API_KEY")
        or os.environ.get("ONTO_LLM_KEY")
        or ""
    ).strip()
    if not base_url and not api_key:
        return None
    return OpenAICompatProvider(
        base_url=base_url or DEFAULT_BASE_URL,
        model=os.environ.get("ONTO_LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        api_key=api_key,
    )
