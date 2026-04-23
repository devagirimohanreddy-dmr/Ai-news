"""Anthropic Claude LLM provider using claude-sonnet-4-20250514."""

from __future__ import annotations

import logging
import time

from anthropic import AsyncAnthropic, APIError, RateLimitError

from src.config.settings import settings
from src.llm.base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# claude-sonnet-4-20250514 pricing (per 1M tokens)
_INPUT_COST_PER_M = 3.0
_OUTPUT_COST_PER_M = 15.0
_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 4096


class ClaudeProvider(BaseLLMProvider):
    """Provider that routes requests to Anthropic's Claude model."""

    def __init__(self) -> None:
        self._api_key = settings.ANTHROPIC_API_KEY
        self._client: AsyncAnthropic | None = None
        if self._api_key:
            self._client = AsyncAnthropic(api_key=self._api_key)

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
            raise RuntimeError("Claude provider is not configured (missing API key).")

        # Claude does not have a native JSON mode; instruct via system prompt.
        effective_system = system_prompt
        if json_mode:
            suffix = "Respond in JSON format."
            if effective_system:
                effective_system = f"{effective_system}\n{suffix}"
            else:
                effective_system = suffix

        kwargs: dict = {
            "model": _MODEL,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if effective_system:
            kwargs["system"] = effective_system

        start = time.perf_counter()
        try:
            response = await self._client.messages.create(**kwargs)
        except RateLimitError as exc:
            logger.warning("Claude rate-limited: %s", exc)
            raise
        except APIError as exc:
            logger.error("Claude API error: %s", exc)
            raise

        latency_ms = (time.perf_counter() - start) * 1000

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        total_tokens = input_tokens + output_tokens

        cost = (
            input_tokens * _INPUT_COST_PER_M / 1_000_000
            + output_tokens * _OUTPUT_COST_PER_M / 1_000_000
        )

        # Extract text from content blocks.
        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        return LLMResponse(
            text=text,
            provider="claude",
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
