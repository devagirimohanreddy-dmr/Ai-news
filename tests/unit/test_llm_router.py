"""Unit tests for the LLM Router and provider fallback chain."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.llm.base import BaseLLMProvider, LLMResponse
from src.llm.router import LLMRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(provider: str = "mock", model: str = "mock-1") -> LLMResponse:
    return LLMResponse(
        text="hello",
        provider=provider,
        model=model,
        tokens_used=10,
        latency_ms=50.0,
        cost_estimate=0.0001,
    )


def _cache_key(prompt: str, system_prompt: str, json_mode: bool) -> str:
    raw = f"{prompt}|{system_prompt}|{json_mode}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"llm:cache:{digest}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def router() -> LLMRouter:
    """Return a fresh LLMRouter with Redis disabled."""
    with (
        patch("src.llm.openai_provider.settings") as openai_settings,
        patch("src.llm.claude_provider.settings") as claude_settings,
        patch("src.llm.gemini_provider.settings") as gemini_settings,
        patch("src.llm.ollama_provider.settings") as ollama_settings,
        patch("src.llm.router.settings") as router_settings,
    ):
        # Disable all real API keys so constructors don't try to create clients.
        openai_settings.OPENAI_API_KEY = None
        claude_settings.ANTHROPIC_API_KEY = None
        gemini_settings.GOOGLE_API_KEY = None
        ollama_settings.OLLAMA_BASE_URL = "http://localhost:11434"
        router_settings.REDIS_URL = "redis://localhost:6379/0"

        r = LLMRouter()
        # Disable Redis for most tests.
        r._redis = None
        yield r
        await r.close()


# ---------------------------------------------------------------------------
# Tests: Fallback chain
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Verify that when the primary provider fails, the router falls back."""

    @pytest.mark.asyncio
    async def test_falls_back_on_primary_failure(self, router: LLMRouter) -> None:
        """If OpenAI fails, router should try Claude next."""
        # Make OpenAI available but raise on generate.
        router._openai.is_available = AsyncMock(return_value=True)
        router._openai.generate = AsyncMock(side_effect=RuntimeError("boom"))

        # Claude succeeds.
        expected = _make_response(provider="claude", model="claude-sonnet-4-20250514")
        router._claude.is_available = AsyncMock(return_value=True)
        router._claude.generate = AsyncMock(return_value=expected)

        # Remaining providers not needed but mark unavailable.
        router._gemini.is_available = AsyncMock(return_value=False)
        router._ollama.is_available = AsyncMock(return_value=False)

        result = await router.generate("test prompt", task_type="general")
        assert result.provider == "claude"
        router._openai.generate.assert_awaited_once()
        router._claude.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_unavailable_providers(self, router: LLMRouter) -> None:
        """Unavailable providers are skipped entirely."""
        router._openai.is_available = AsyncMock(return_value=False)
        router._claude.is_available = AsyncMock(return_value=False)
        router._gemini.is_available = AsyncMock(return_value=False)

        expected = _make_response(provider="ollama")
        router._ollama.is_available = AsyncMock(return_value=True)
        router._ollama.generate = AsyncMock(return_value=expected)

        result = await router.generate("hi")
        assert result.provider == "ollama"

    @pytest.mark.asyncio
    async def test_falls_through_full_chain(self, router: LLMRouter) -> None:
        """Falls from OpenAI -> Claude -> Gemini -> Ollama."""
        router._openai.is_available = AsyncMock(return_value=True)
        router._openai.generate = AsyncMock(side_effect=RuntimeError("fail"))

        router._claude.is_available = AsyncMock(return_value=True)
        router._claude.generate = AsyncMock(side_effect=RuntimeError("fail"))

        router._gemini.is_available = AsyncMock(return_value=True)
        router._gemini.generate = AsyncMock(side_effect=RuntimeError("fail"))

        expected = _make_response(provider="ollama")
        router._ollama.is_available = AsyncMock(return_value=True)
        router._ollama.generate = AsyncMock(return_value=expected)

        result = await router.generate("test")
        assert result.provider == "ollama"


# ---------------------------------------------------------------------------
# Tests: Task routing
# ---------------------------------------------------------------------------

class TestTaskRouting:
    """Verify task_type selects the correct primary provider."""

    @pytest.mark.asyncio
    async def test_summarize_routes_to_openai(self, router: LLMRouter) -> None:
        expected = _make_response(provider="openai")
        router._openai.is_available = AsyncMock(return_value=True)
        router._openai.generate = AsyncMock(return_value=expected)

        # Other providers not needed.
        router._claude.is_available = AsyncMock(return_value=False)
        router._gemini.is_available = AsyncMock(return_value=False)
        router._ollama.is_available = AsyncMock(return_value=False)

        result = await router.generate("summarize this", task_type="summarize")
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_classify_routes_to_ollama(self, router: LLMRouter) -> None:
        expected = _make_response(provider="ollama")
        router._ollama.is_available = AsyncMock(return_value=True)
        router._ollama.generate = AsyncMock(return_value=expected)

        # Other providers not needed.
        router._openai.is_available = AsyncMock(return_value=False)
        router._claude.is_available = AsyncMock(return_value=False)
        router._gemini.is_available = AsyncMock(return_value=False)

        result = await router.generate("classify this", task_type="classify")
        assert result.provider == "ollama"

    @pytest.mark.asyncio
    async def test_score_routes_to_cheapest(self, router: LLMRouter) -> None:
        """Score should prefer Ollama (free), then Gemini."""
        router._ollama.is_available = AsyncMock(return_value=False)

        expected = _make_response(provider="gemini")
        router._gemini.is_available = AsyncMock(return_value=True)
        router._gemini.generate = AsyncMock(return_value=expected)

        router._openai.is_available = AsyncMock(return_value=False)
        router._claude.is_available = AsyncMock(return_value=False)

        result = await router.generate("score this", task_type="score")
        assert result.provider == "gemini"

    @pytest.mark.asyncio
    async def test_search_routes_to_openai_or_claude(self, router: LLMRouter) -> None:
        router._openai.is_available = AsyncMock(return_value=False)

        expected = _make_response(provider="claude")
        router._claude.is_available = AsyncMock(return_value=True)
        router._claude.generate = AsyncMock(return_value=expected)

        router._gemini.is_available = AsyncMock(return_value=False)
        router._ollama.is_available = AsyncMock(return_value=False)

        result = await router.generate("search this", task_type="search")
        assert result.provider == "claude"


# ---------------------------------------------------------------------------
# Tests: Caching
# ---------------------------------------------------------------------------

class TestCaching:
    """Verify Redis-based caching behaviour."""

    @pytest.mark.asyncio
    async def test_returns_cached_response(self, router: LLMRouter) -> None:
        """Second identical call returns the cached response."""
        cached = _make_response(provider="openai")
        cached_json = json.dumps(
            {
                "text": cached.text,
                "provider": cached.provider,
                "model": cached.model,
                "tokens_used": cached.tokens_used,
                "latency_ms": cached.latency_ms,
                "cost_estimate": cached.cost_estimate,
            }
        )

        # Fake Redis that returns the cached value.
        fake_redis = AsyncMock()
        fake_redis.ping = AsyncMock()
        fake_redis.get = AsyncMock(return_value=cached_json)
        router._redis = fake_redis

        # None of the providers should be called.
        router._openai.is_available = AsyncMock(return_value=True)
        router._openai.generate = AsyncMock(
            side_effect=AssertionError("should not be called"),
        )

        result = await router.generate("test prompt")
        assert result.provider == "openai"
        assert result.text == "hello"
        router._openai.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stores_response_in_cache(self, router: LLMRouter) -> None:
        """Successful generation stores the result in Redis."""
        fake_redis = AsyncMock()
        fake_redis.ping = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)  # Cache miss
        fake_redis.set = AsyncMock()
        router._redis = fake_redis

        expected = _make_response(provider="openai")
        router._openai.is_available = AsyncMock(return_value=True)
        router._openai.generate = AsyncMock(return_value=expected)
        router._claude.is_available = AsyncMock(return_value=False)
        router._gemini.is_available = AsyncMock(return_value=False)
        router._ollama.is_available = AsyncMock(return_value=False)

        await router.generate("test prompt")

        # Verify set was called with proper key and TTL.
        fake_redis.set.assert_awaited_once()
        call_args = fake_redis.set.call_args
        key = call_args[0][0]
        assert key == _cache_key("test prompt", "", False)
        assert call_args[1]["ex"] == 3600


# ---------------------------------------------------------------------------
# Tests: All providers unavailable
# ---------------------------------------------------------------------------

class TestAllUnavailable:
    """When every provider is unavailable or fails, an error is raised."""

    @pytest.mark.asyncio
    async def test_raises_when_all_unavailable(self, router: LLMRouter) -> None:
        router._openai.is_available = AsyncMock(return_value=False)
        router._claude.is_available = AsyncMock(return_value=False)
        router._gemini.is_available = AsyncMock(return_value=False)
        router._ollama.is_available = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await router.generate("hello")

    @pytest.mark.asyncio
    async def test_raises_when_all_fail(self, router: LLMRouter) -> None:
        for provider in router._fallback_chain:
            provider.is_available = AsyncMock(return_value=True)
            provider.generate = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await router.generate("hello")
