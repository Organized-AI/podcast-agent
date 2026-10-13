"""OpenAI-compatible Chat Completions provider.

One class covers OpenAI, Ollama (http://localhost:11434/v1), vLLM, LM Studio and any other
server that speaks the /chat/completions protocol — which is how the agent runs on a fully
local model.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from podcast_agent.llm.base import LLMError, LLMProvider, LLMResponse, estimate_tokens

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        fast_model: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 90,
        max_tokens_param: str = "max_tokens",
        max_retries: int = 3,
    ):
        super().__init__(model, fast_model)
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.max_tokens_param = max_tokens_param
        self.max_retries = max_retries
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(timeout=timeout_s, headers=headers)

    def _complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float | None,
        cache_prefix: str | None,
        task: str,
        context: dict[str, Any],
    ) -> LLMResponse:
        full_system = f"{cache_prefix}\n\n{system}" if cache_prefix else system
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": full_system}, *messages],
            self.max_tokens_param: max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        data = self._post_with_retries(payload)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape from {self.base_url}: {str(data)[:300]}") from exc
        usage = data.get("usage") or {}
        prompt_text = full_system + "".join(m["content"] for m in messages)
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=usage.get("prompt_tokens") or estimate_tokens(prompt_text),
            output_tokens=usage.get("completion_tokens") or estimate_tokens(text),
            estimated=not usage,
            stop_reason=choice.get("finish_reason"),
        )

    def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.post(url, json=payload)
                if r.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(f"retryable status {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in RETRYABLE_STATUS:
                    break
                if attempt < self.max_retries:
                    time.sleep(min(8.0, 0.5 * 2**attempt))
        raise LLMError(f"{self.name} request failed: {last_error}")
