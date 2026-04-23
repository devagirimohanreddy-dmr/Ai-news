"""Google Gemini LLM provider using gemini-2.0-flash."""

from __future__ import annotations

import asyncio
import logging
import time
from functools import partial

import google.generativeai as genai

from src.config.settings import settings
from src.llm.base import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# gemini-2.0-flash pricing (per 1M tokens)
_INPUT_COST_PER_M = 0.075
_OUTPUT_COST_PER_M = 0.30
_MODEL = "gemini-2.0-flash"


class GeminiProvider(BaseLLMProvider):
    """Provider that routes requests to Google's Gemini model.

    The ``google-generativeai`` SDK is synchronous, so calls are wrapped
    with :func:`asyncio.get_event_loop().run_in_executor` to avoid
    blocking the event loop.
    """

    def __init__(self) -> None:
        self._api_key = settings.GOOGLE_API_KEY
        self._model: genai.GenerativeModel | None = None
        if self._api_key:
            genai.configure(api_key=self._api_key)
            self._model = genai.GenerativeModel(_MODEL)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_sync(
        self,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
    ) -> tuple[str, int, int]:
        """Run the blocking Gemini SDK call.  Returns (text, input_tokens, output_tokens)."""
        if self._model is None:
            raise RuntimeError("Gemini provider is not configured (missing API key).")

        generation_config: dict = {}
        if json_mode:
            generation_config["response_mime_type"] = "application/json"

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        response = self._model.generate_content(
            full_prompt,
            generation_config=generation_config if generation_config else None,
        )

        text = response.text or ""

        # Token counts from usage_metadata (may not always be present).
        metadata = getattr(response, "usage_metadata", None)
        input_tokens = getattr(metadata, "prompt_token_count", 0) or 0
        output_tokens = getattr(metadata, "candidates_token_count", 0) or 0

        return text, input_tokens, output_tokens

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
    ) -> LLMResponse:
        if self._model is None:
            raise RuntimeError("Gemini provider is not configured (missing API key).")

        loop = asyncio.get_running_loop()
        start = time.perf_counter()

        try:
            text, input_tokens, output_tokens = await loop.run_in_executor(
                None,
                partial(self._generate_sync, prompt, system_prompt, json_mode),
            )
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        total_tokens = input_tokens + output_tokens

        cost = (
            input_tokens * _INPUT_COST_PER_M / 1_000_000
            + output_tokens * _OUTPUT_COST_PER_M / 1_000_000
        )

        return LLMResponse(
            text=text,
            provider="gemini",
            model=_MODEL,
            tokens_used=total_tokens,
            latency_ms=round(latency_ms, 2),
            cost_estimate=round(cost, 8),
        )

    async def is_available(self) -> bool:
        return self._api_key is not None and len(self._api_key) > 0

    async def close(self) -> None:
        # The google-generativeai SDK does not expose a close/cleanup method.
        self._model = None
