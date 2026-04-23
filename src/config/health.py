"""Health-check helpers for all external dependencies.

Provides individual checkers for Postgres, Redis, Firecrawl, and Ollama,
plus a top-level ``check_health()`` that aggregates them into a single
status payload suitable for the ``/health`` HTTP endpoint.

Usage in FastAPI:

    from src.config.health import check_health

    @app.get("/health")
    async def health():
        return await check_health()
"""

from __future__ import annotations

import logging
import time

import httpx
from sqlalchemy import text

from src.config.settings import settings
from src.models.base import get_session_factory

logger = logging.getLogger(__name__)

# Timeout (seconds) for HTTP dependency checks.
_HTTP_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Individual dependency checkers
# ---------------------------------------------------------------------------


async def check_postgres() -> dict:
    """Check PostgreSQL connectivity with a simple ``SELECT 1``."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            start = time.monotonic()
            await session.execute(text("SELECT 1"))
            latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as exc:
        logger.warning("Health check failed: postgres — %s", exc)
        return {"status": "error", "error": str(exc)}


async def check_redis() -> dict:
    """Check Redis connectivity with a ``PING`` command."""
    try:
        import redis.asyncio as aioredis

        start = time.monotonic()
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            pong = await r.ping()
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            if pong:
                return {"status": "ok", "latency_ms": latency_ms}
            return {"status": "error", "error": "PING returned False"}
        finally:
            await r.aclose()
    except Exception as exc:
        logger.warning("Health check failed: redis — %s", exc)
        return {"status": "error", "error": str(exc)}


async def check_firecrawl() -> dict:
    """Check Firecrawl service availability with ``GET /``."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            start = time.monotonic()
            resp = await client.get(f"{settings.FIRECRAWL_BASE_URL}/")
            latency_ms = round((time.monotonic() - start) * 1000, 1)
        if resp.status_code < 500:
            return {"status": "ok", "latency_ms": latency_ms}
        return {
            "status": "error",
            "error": f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        logger.warning("Health check failed: firecrawl — %s", exc)
        return {"status": "error", "error": str(exc)}


async def check_ollama() -> dict:
    """Check Ollama service availability with ``GET /api/tags``."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            start = time.monotonic()
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            latency_ms = round((time.monotonic() - start) * 1000, 1)
        if resp.status_code == 200:
            # Optionally surface the list of loaded models.
            data = resp.json()
            models = [m.get("name", "?") for m in data.get("models", [])]
            return {
                "status": "ok",
                "latency_ms": latency_ms,
                "models": models,
            }
        return {
            "status": "error",
            "error": f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        logger.warning("Health check failed: ollama — %s", exc)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Aggregated health endpoint
# ---------------------------------------------------------------------------


async def check_health() -> dict:
    """Check health of all dependencies and return an aggregate status.

    Returns:
        A dict with ``"status"`` (``"healthy"`` or ``"degraded"``) and
        ``"checks"`` mapping each dependency name to its individual result.
    """
    checks = {
        "database": await check_postgres(),
        "redis": await check_redis(),
        "firecrawl": await check_firecrawl(),
        "ollama": await check_ollama(),
    }

    all_ok = all(c["status"] == "ok" for c in checks.values())
    overall = "healthy" if all_ok else "degraded"

    return {
        "status": overall,
        "checks": checks,
    }
