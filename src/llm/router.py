"""LLM Router — routes tasks to the best provider with fallback and caching."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Sequence

import redis.asyncio as redis

from src.config.settings import settings
from src.llm.base import BaseLLMProvider, LLMResponse
from src.llm.openai_provider import OpenAIProvider
from src.llm.claude_provider import ClaudeProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 3600  # 1 hour


class LLMRouter:
    """Routes LLM tasks to appropriate providers with fallback.

    Provider fallback chain (default): OpenAI -> Claude -> Gemini -> Ollama.
    Task-based routing selects the *primary* provider; if it fails, the
    remaining providers in the fallback chain are tried in order.
    """

    def __init__(self) -> None:
        # Instantiate every provider.
        self._openai = OpenAIProvider()
        self._claude = ClaudeProvider()
        self._gemini = GeminiProvider()
        self._ollama = OllamaProvider()

        # Canonical fallback order.
        self._fallback_chain: list[BaseLLMProvider] = [
            self._openai,
            self._claude,
            self._gemini,
            self._ollama,
        ]

        # Redis client for response caching.
        self._redis: redis.Redis | None = None

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    async def _get_redis(self) -> redis.Redis | None:
        """Lazily initialise and return the Redis connection."""
        if self._redis is not None:
            return self._redis
        try:
            self._redis = redis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            await self._redis.ping()
            return self._redis
        except Exception as exc:
            logger.warning("Redis unavailable — caching disabled: %s", exc)
            self._redis = None
            return None

    @staticmethod
    def _cache_key(prompt: str, system_prompt: str, json_mode: bool) -> str:
        raw = f"{prompt}|{system_prompt}|{json_mode}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"llm:cache:{digest}"

    async def _get_cached(
        self, prompt: str, system_prompt: str, json_mode: bool
    ) -> LLMResponse | None:
        r = await self._get_redis()
        if r is None:
            return None
        key = self._cache_key(prompt, system_prompt, json_mode)
        try:
            data = await r.get(key)
            if data is None:
                return None
            payload = json.loads(data)
            return LLMResponse(**payload)
        except Exception as exc:
            logger.debug("Cache read failed: %s", exc)
            return None

    async def _set_cached(
        self,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        response: LLMResponse,
    ) -> None:
        r = await self._get_redis()
        if r is None:
            return
        key = self._cache_key(prompt, system_prompt, json_mode)
        payload = json.dumps(
            {
                "text": response.text,
                "provider": response.provider,
                "model": response.model,
                "tokens_used": response.tokens_used,
                "latency_ms": response.latency_ms,
                "cost_estimate": response.cost_estimate,
            }
        )
        try:
            await r.set(key, payload, ex=_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Cache write failed: %s", exc)

    # ------------------------------------------------------------------
    # Routing logic
    # ------------------------------------------------------------------

    def _route_for_task(self, task_type: str) -> Sequence[BaseLLMProvider]:
        """Return an ordered list of providers for *task_type*.

        The first entry is the preferred provider; the rest form the
        fallback chain (preserving the canonical order but de-duplicated).
        """
        primary: BaseLLMProvider

        if task_type == "summarize":
            primary = self._openai
        elif task_type == "classify":
            primary = self._ollama
        elif task_type == "score":
            # Cheapest available — Ollama (free), then Gemini, OpenAI, Claude.
            return [self._ollama, self._gemini, self._openai, self._claude]
        elif task_type == "search":
            # OpenAI first, then Claude.
            return self._dedup(
                [self._openai, self._claude], self._fallback_chain
            )
        else:
            # "general" or unknown — default fallback chain.
            return list(self._fallback_chain)

        return self._dedup([primary], self._fallback_chain)

    @staticmethod
    def _dedup(
        preferred: list[BaseLLMProvider],
        fallback: list[BaseLLMProvider],
    ) -> list[BaseLLMProvider]:
        """Merge *preferred* + *fallback* with no duplicates, keeping order."""
        seen: set[int] = set()
        result: list[BaseLLMProvider] = []
        for p in preferred + fallback:
            pid = id(p)
            if pid not in seen:
                seen.add(pid)
                result.append(p)
        return result

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
        task_type: str = "general",
    ) -> LLMResponse:
        """Route to the best provider based on *task_type*, with fallback.

        Args:
            prompt: User prompt.
            system_prompt: System instruction.
            json_mode: Request JSON output.
            task_type: One of ``"summarize"``, ``"classify"``, ``"score"``,
                       ``"search"``, or ``"general"``.

        Returns:
            :class:`LLMResponse` from the first provider that succeeds.

        Raises:
            RuntimeError: When every provider in the chain fails.
        """
        # 1. Check cache.
        cached = await self._get_cached(prompt, system_prompt, json_mode)
        if cached is not None:
            logger.info("Cache hit for prompt (provider=%s)", cached.provider)
            return cached

        # 2. Determine provider order.
        chain = self._route_for_task(task_type)

        # 3. Try each provider.
        last_error: Exception | None = None
        for provider in chain:
            if not await provider.is_available():
                logger.debug(
                    "Skipping unavailable provider %s",
                    provider.__class__.__name__,
                )
                continue
            try:
                response = await provider.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    json_mode=json_mode,
                )
                logger.info(
                    "LLM response from %s (%s) — %d tokens, %.1f ms",
                    response.provider,
                    response.model,
                    response.tokens_used,
                    response.latency_ms,
                )
                # 4. Store in cache.
                await self._set_cached(prompt, system_prompt, json_mode, response)
                return response
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Provider %s failed: %s — trying next",
                    provider.__class__.__name__,
                    exc,
                )

        raise RuntimeError(
            f"All LLM providers failed. Last error: {last_error}"
        )

    async def close(self) -> None:
        """Close all providers and the Redis connection."""
        for provider in self._fallback_chain:
            try:
                await provider.close()
            except Exception as exc:
                logger.debug("Error closing %s: %s", provider.__class__.__name__, exc)

        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
