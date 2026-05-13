"""Celery tasks for scraping news sources."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from src.config.celery_app import app
from src.config.settings import settings
from src.models.source import Source
from src.pipeline.orchestrator import ArticlePipeline
from src.scrapers.registry import ScraperRegistry

logger = logging.getLogger(__name__)

# Fallback default — sources without a `schedule_cron` value are scraped
# every two hours. Used by ``_is_due``.
_DEFAULT_CRON = "0 */2 * * *"


def _is_due(cron_expr: str | None, last_scraped_at: datetime | None) -> bool:
    """Return True if a source whose cron is ``cron_expr`` is due to run now.

    Uses ``croniter`` if available — falls back to "always due" if not (so
    we never accidentally stop scraping if the dep is missing).
    """
    try:
        from croniter import croniter
    except ImportError:
        return True
    expr = (cron_expr or _DEFAULT_CRON).strip() or _DEFAULT_CRON
    now = datetime.now(timezone.utc)
    # If we've never scraped, run immediately.
    if last_scraped_at is None:
        return True
    last = last_scraped_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    try:
        next_due = croniter(expr, last).get_next(datetime)
    except Exception:
        # Bad cron string — fall back to "run".
        return True
    # Add tzinfo if croniter returned naive.
    if next_due.tzinfo is None:
        next_due = next_due.replace(tzinfo=timezone.utc)
    return next_due <= now


def _make_session_factory():
    """Create a fresh async engine + session factory with NullPool.

    NullPool prevents asyncpg connection-pool state from being shared
    across Celery's forked worker processes.
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory, engine


async def _scrape_source_async(source_id: int) -> dict:
    """Core async logic for scraping a single source."""
    session_factory, engine = _make_session_factory()
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Source).where(Source.id == source_id)
            )
            source = result.scalar_one_or_none()

            if source is None:
                logger.error("Source id=%s not found in database", source_id)
                return {"source_id": source_id, "status": "not_found", "articles": 0}

            if not source.enabled:
                logger.info("Source id=%s (%s) is disabled, skipping", source_id, source.name)
                return {"source_id": source_id, "status": "disabled", "articles": 0}

            scraper = None
            try:
                # Build effective scraper type and config.
                scraper_type = source.scraper_type
                config = dict(source.config_json or {})

                # "api" sources store the real sub-type in config_json["type"]
                if scraper_type == "api" and "type" in config:
                    scraper_type = config["type"]

                # RSS sources seeded without feed_url fall back to source URL
                if scraper_type == "rss" and "feed_url" not in config:
                    config["feed_url"] = source.url

                scraper = ScraperRegistry.get(scraper_type, config)

                logger.info(
                    "Scraping source id=%s name=%s type=%s",
                    source.id, source.name, scraper_type,
                )

                raw_articles = await scraper.scrape()
                logger.info(
                    "Source id=%s returned %d raw articles", source.id, len(raw_articles),
                )

                pipeline = ArticlePipeline(session=session, source_id=source.id)
                processed = await pipeline.process_batch(raw_articles)

                source.last_scraped_at = datetime.utcnow()
                source.error_count = 0
                await session.commit()

                logger.info(
                    "Source id=%s scrape complete: %d/%d articles processed",
                    source.id, len(processed), len(raw_articles),
                )

                return {
                    "source_id": source_id,
                    "status": "success",
                    "articles": len(processed),
                    "raw_count": len(raw_articles),
                }

            except Exception as exc:
                logger.error(
                    "Error scraping source id=%s: %s", source_id, exc, exc_info=True,
                )
                source.error_count = (source.error_count or 0) + 1
                try:
                    await session.commit()
                except Exception:
                    logger.error(
                        "Failed to update error_count for source id=%s",
                        source_id, exc_info=True,
                    )
                return {
                    "source_id": source_id,
                    "status": "error",
                    "error": str(exc),
                    "articles": 0,
                }

            finally:
                if scraper is not None:
                    try:
                        await scraper.close()
                    except Exception:
                        logger.debug(
                            "Error closing scraper for source id=%s",
                            source_id, exc_info=True,
                        )
    finally:
        await engine.dispose()


async def _scrape_all_sources_async() -> list[tuple[int, str, bool]]:
    """Return ``(id, name, due)`` for every enabled source.

    A source is considered "due" when its ``schedule_cron`` expression
    fires given its ``last_scraped_at``. Sources that are not due are
    returned with ``due=False`` so the caller can log them but skip
    dispatch.
    """
    session_factory, engine = _make_session_factory()
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Source).where(Source.enabled == True)  # noqa: E712
            )
            sources = result.scalars().all()
        return [
            (s.id, s.name, _is_due(s.schedule_cron, s.last_scraped_at))
            for s in sources
        ]
    finally:
        await engine.dispose()


@app.task(name="src.scheduler.scrape_tasks.scrape_source", bind=True, max_retries=2)
def scrape_source(self, source_id: int) -> dict:
    """Celery task: scrape a single source by its database ID."""
    try:
        return asyncio.run(_scrape_source_async(source_id))
    except Exception as exc:
        logger.error(
            "scrape_source task failed for source_id=%s: %s",
            source_id, exc, exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@app.task(name="src.scheduler.scrape_tasks.scrape_all_sources")
def scrape_all_sources() -> dict:
    """Celery task: dispatch ``scrape_source`` for sources whose cron is due."""
    items = asyncio.run(_scrape_all_sources_async())
    due = [(sid, name) for (sid, name, is_due) in items if is_due]
    skipped = [name for (_, name, is_due) in items if not is_due]
    logger.info(
        "Beat tick: %d enabled sources, %d due, %d skipped (%s)",
        len(items), len(due), len(skipped), ", ".join(skipped[:10]),
    )
    for sid, _name in due:
        scrape_source.delay(sid)
    return {
        "dispatched": len(due),
        "skipped": len(skipped),
        "source_ids": [sid for sid, _ in due],
    }
