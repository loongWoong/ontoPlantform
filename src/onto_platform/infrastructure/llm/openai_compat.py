"""OpenAICompatProvider: one provider for OpenAI / Qwen / GLM / vLLM / Ollama.

All of them speak the ``/chat/completions`` protocol; only base_url,
api_key and model change (design.txt §27: Domain 不依赖具体模型).
"""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit, urlunsplit

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def chat_completions_url(base_url: str) -> str:
    """Build the endpoint URL from a user-supplied base URL.

    The settings field is documented as the OpenAI-compatible *base* URL
    (``https://api.openai.com/v1``), but users routinely enter only the host
    (``http://127.0.0.1:8000``).  Treating that literally called
    ``/chat/completions`` instead of ``/v1/chat/completions``, so the model
    endpoint returned 404 and the caller silently fell back.  Only an empty
    path is defaulted to ``/v1``; a custom proxy path is preserved.

    A base URL that already ends in ``/chat/completions`` is accepted too,
    so a copied full endpoint does not become ``.../chat/completions/
    chat/completions``.
    """
    base = (base_url or "").strip()
    if not base:
        base = DEFAULT_BASE_URL
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    suffix = "/chat/completions"
    if path.endswith(suffix):
        path = path[:-len(suffix)].rstrip("/")
    if not path:
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc,
                      path + suffix, parts.query, ""))


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> None:
        self._url = chat_completions_url(base_url)
        self._model = model
        # Public label for diagnostics/traces; never expose the key.
        self.model = model
        self._api_key = api_key
        self._temperature = temperature
        self._timeout = timeout

    def complete(self, messages) -> object:
        from ...ports.llm import LLMResponse

        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": self._temperature,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(self._url, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return LLMResponse(content=content)
