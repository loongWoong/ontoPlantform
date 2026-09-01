"""OpenAICompatProvider: one provider for OpenAI / Qwen / GLM / vLLM / Ollama.

All of them speak the ``/chat/completions`` protocol; only base_url,
api_key and model change (design.txt §27: Domain 不依赖具体模型).
"""

from __future__ import annotations

import json
import urllib.request


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
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
