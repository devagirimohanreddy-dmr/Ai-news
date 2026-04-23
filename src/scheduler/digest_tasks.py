"""Celery tasks for generating and posting the daily digest."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config.celery_app import app
from src.models.article import Article
from src.models.base import get_session_factory
from src.models.post_log import PostLog

logger = logging.getLogger(__name__)

# Number of top stories to feature in the digest.
_TOP_STORIES_COUNT = 5


async def _generate_daily_digest_async() -> dict:
    """Core async logic for building the daily digest."""
    session_factory = get_session_factory()
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)

    async with session_factory() as session:
        # Find articles that completed the pipeline in the last 24 hours.
        result = await session.execute(
            select(Article)
            .options(
                selectinload(Article.categories),
                selectinload(Article.summaries),
            )
            .where(
                Article.pipeline_status == "routed",
                Article.created_at >= cutoff,
            )
            .order_by(Article.importance_score.desc())
        )
        articles = result.scalars().all()

        if not articles:
            logger.info("No routed articles found in the last 24h — skipping digest")
            return {
                "date": now.strftime("%Y-%m-%d"),
                "total_articles": 0,
                "top_stories": [],
                "categories": {},
            }

        # --- Build category buckets -----------------------------------------
        categories: dict[str, list[dict]] = {}
        all_scored: list[tuple[int, dict, Article]] = []

        for article in articles:
            # Pick the best summary (most recent).
            summary_text = ""
            headline = article.title
            if article.summaries:
                best = sorted(article.summaries, key=lambda s: s.created_at, reverse=True)[0]
                summary_text = best.summary_text
                headline = best.headline or article.title

            article_data = {
                "title": headline,
                "summary": summary_text,
                "url": article.url,
                "score": article.importance_score,
            }

            all_scored.append((article.importance_score, article_data, article))

            # Group into categories.
            if article.categories:
                for cat in article.categories:
                    categories.setdefault(cat.name, []).append(article_data)
            else:
                categories.setdefault("Uncategorised", []).append(article_data)

        # Sort each category by score descending.
        for cat_name in categories:
            categories[cat_name].sort(key=lambda a: a["score"], reverse=True)

        # --- Pick top stories -----------------------------------------------
        all_scored.sort(key=lambda t: t[0], reverse=True)
        top_stories = [item[1] for item in all_scored[:_TOP_STORIES_COUNT]]

        digest_data = {
            "date": now.strftime("%Y-%m-%d"),
            "total_articles": len(articles),
            "top_stories": top_stories,
            "categories": categories,
        }

        # --- Create PostLog entry -------------------------------------------
        post_log = PostLog(
            article_id=None,
            post_type="digest",
            status="pending",
        )
        session.add(post_log)
        await session.commit()

        logger.info(
            "Daily digest built: %d articles, %d categories, %d top stories",
            len(articles),
            len(categories),
            len(top_stories),
        )

        return digest_data


@app.task(name="src.scheduler.digest_tasks.generate_daily_digest")
def generate_daily_digest() -> dict:
    """Celery task: generate the daily digest and dispatch it for posting.

    Queries articles where pipeline_status='routed' and created_at is within
    the last 24 hours. Groups by category, picks top stories, builds the
    digest structure, and dispatches the post_digest task.
    """
    digest_data = asyncio.run(_generate_daily_digest_async())

    if digest_data["total_articles"] > 0:
        # Dispatch the posting task (Teams posting implemented in Phase 6).
        app.send_task(
            "src.scheduler.digest_tasks.post_digest",
            args=[digest_data],
        )

    return digest_data


@app.task(name="src.scheduler.digest_tasks.post_digest")
def post_digest(digest_data: dict) -> dict:
    """Celery task: post the digest to the configured channel.

    Called by generate_daily_digest. For now, logs the digest and marks it
    as pending (actual Teams posting will be implemented in Phase 6).
    """
    logger.info(
        "Digest ready for posting — date=%s, total_articles=%d, top_stories=%d, categories=%d",
        digest_data.get("date"),
        digest_data.get("total_articles", 0),
        len(digest_data.get("top_stories", [])),
        len(digest_data.get("categories", {})),
    )

    # Log top stories for visibility.
    for i, story in enumerate(digest_data.get("top_stories", []), 1):
        logger.info(
            "  Top #%d: [score=%s] %s",
            i,
            story.get("score"),
            story.get("title"),
        )

    return {"status": "pending", "date": digest_data.get("date")}
