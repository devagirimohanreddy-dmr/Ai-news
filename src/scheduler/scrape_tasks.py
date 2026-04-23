"""Celery tasks for scraping news sources."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.config.celery_app import app
from src.models.base import get_session_factory
from src.models.source import Source
from src.pipeline.orchestrator import ArticlePipeline
from src.scrapers.registry import ScraperRegistry

logger = logging.getLogger(__name__)


async def _scrape_source_async(source_id: int) -> dict:
    """Core async logic for scraping a single source."""
    session_factory = get_session_factory()

    async with session_factory() as session:
        # Load the source from the database.
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
            # Get a scraper instance for this source type.
            scraper = ScraperRegistry.get(source.scraper_type, source.config_json)

            logger.info(
                "Scraping source id=%s name=%s type=%s",
                source.id, source.name, source.scraper_type,
            )

            # Run the scraper.
            raw_articles = await scraper.scrape()
            logger.info(
                "Source id=%s returned %d raw articles", source.id, len(raw_articles),
            )

            # Feed results through the pipeline.
            pipeline = ArticlePipeline(session=session)
            processed = await pipeline.process_batch(raw_articles)

            # Update source metadata on success.
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

            # Increment error count.
            source.error_count = (source.error_count or 0) + 1
            try:
                await session.commit()
            except Exception:
                logger.error(
                    "Failed to update error_count for source id=%s",
                    source_id,
                    exc_info=True,
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
                        source_id,
                        exc_info=True,
                    )


@app.task(name="src.scheduler.scrape_tasks.scrape_source", bind=True, max_retries=2)
def scrape_source(self, source_id: int) -> dict:
    """Celery task: scrape a single source by its database ID.

    Loads the source, runs the appropriate scraper, feeds raw articles
    through the ArticlePipeline, and updates source metadata.
    """
    try:
        return asyncio.run(_scrape_source_async(source_id))
    except Exception as exc:
        logger.error(
            "scrape_source task failed for source_id=%s: %s",
            source_id, exc, exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


async def _scrape_all_sources_async() -> list[int]:
    """Query all enabled sources and return their IDs."""
    session_factory = get_session_factory()

    async with session_factory() as session:
        result = await session.execute(
            select(Source.id).where(Source.enabled == True)  # noqa: E712
        )
        source_ids = [row[0] for row in result.all()]

    return source_ids


@app.task(name="src.scheduler.scrape_tasks.scrape_all_sources")
def scrape_all_sources() -> dict:
    """Celery task: dispatch scrape_source subtasks for every enabled source.

    This is the task invoked by the periodic beat schedule. It queries all
    enabled sources and fans out one ``scrape_source.delay()`` call per
    source so they are processed in parallel across workers.
    """
    source_ids = asyncio.run(_scrape_all_sources_async())

    logger.info("Dispatching scrape tasks for %d enabled sources", len(source_ids))

    for sid in source_ids:
        scrape_source.delay(sid)

    return {"dispatched": len(source_ids), "source_ids": source_ids}
