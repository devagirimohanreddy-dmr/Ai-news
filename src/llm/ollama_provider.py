"""Ollama local-LLM provider using the REST API."""

from __future__ import annotations

import logging
import time

import httpx

from src.config.settings import settings
from src.llm.base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

_MODEL = "llama3.2:3b"
_TIMEOUT = 120.0  # seconds — local models can be slow on first load


class OllamaProvider(BaseLLMProvider):
    """Provider that routes requests to a local Ollama instance via its REST API."""

    def __init__(self) -> None:
        self._base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
    ) -> LLMResponse:
        effective_prompt = prompt
        if json_mode:
            effective_prompt = f"{prompt}\nRespond in valid JSON only."

        payload: dict = {
            "model": _MODEL,
            "prompt": effective_prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        url = f"{self._base_url}/api/generate"
        start = time.perf_counter()

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama HTTP error %s: %s", exc.response.status_code, exc)
            raise
        except httpx.RequestError as exc:
            logger.error("Ollama request error: %s", exc)
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        data = response.json()

        text: str = data.get("response", "")
        # Ollama returns token counts when stream=false.
        prompt_tokens: int = data.get("prompt_eval_count", 0) or 0
        output_tokens: int = data.get("eval_count", 0) or 0
        total_tokens = prompt_tokens + output_tokens

        return LLMResponse(
            text=text,
            provider="ollama",
            model=_MODEL,
            tokens_used=total_tokens,
            latency_ms=round(latency_ms, 2),
            cost_estimate=0.0,
        )

    async def is_available(self) -> bool:
        """Check reachability by hitting the /api/tags endpoint."""
        try:
            response = await self._client.get(
                f"{self._base_url}/api/tags", timeout=5.0
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()
