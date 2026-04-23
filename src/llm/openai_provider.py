"""OpenAI LLM provider using gpt-4o-mini."""

from __future__ import annotations

import logging
import time

from openai import AsyncOpenAI, APIError, RateLimitError

from src.config.settings import settings
from src.llm.base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# gpt-4o-mini pricing (per 1M tokens)
_INPUT_COST_PER_M = 0.15
_OUTPUT_COST_PER_M = 0.60
_MODEL = "gpt-4o-mini"


class OpenAIProvider(BaseLLMProvider):
    """Provider that routes requests to OpenAI's gpt-4o-mini model."""

    def __init__(self) -> None:
        self._api_key = settings.OPENAI_API_KEY
        self._client: AsyncOpenAI | None = None
        if self._api_key:
            self._client = AsyncOpenAI(api_key=self._api_key)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
    ) -> LLMResponse:
        if self._client is None:
            raise RuntimeError("OpenAI provider is not configured (missing API key).")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": _MODEL,
            "messages": messages,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            logger.warning("OpenAI rate-limited: %s", exc)
            raise
        except APIError as exc:
            logger.error("OpenAI API error: %s", exc)
            raise

        latency_ms = (time.perf_counter() - start) * 1000

        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        total_tokens = input_tokens + output_tokens

        cost = (
            input_tokens * _INPUT_COST_PER_M / 1_000_000
            + output_tokens * _OUTPUT_COST_PER_M / 1_000_000
        )

        text = response.choices[0].message.content or ""

        return LLMResponse(
            text=text,
            provider="openai",
            model=_MODEL,
            tokens_used=total_tokens,
            latency_ms=round(latency_ms, 2),
            cost_estimate=round(cost, 8),
        )

    async def is_available(self) -> bool:
        return self._api_key is not None and len(self._api_key) > 0

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
