"""Handler for the /digest command."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.category import Category
from src.bot.cards.digest_card import build_digest_card

logger = logging.getLogger(__name__)


async def handle_digest(session: AsyncSession, args: str) -> dict[str, Any]:
    """Generate an on-demand digest of articles from the last 24 hours.

    Args:
        session: Async SQLAlchemy session.
        args: Currently unused (reserved for future options like time range).

    Returns:
        A dict with ``"card"`` (Adaptive Card dict) and ``"text"`` (fallback).
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        # Query articles from the last 24 hours
        stmt = (
            select(Article)
            .where(
                Article.pipeline_status == "routed",
                Article.created_at >= cutoff,
            )
            .order_by(Article.importance_score.desc(), Article.created_at.desc())
        )
        result = await session.execute(stmt)
        articles = list(result.scalars().unique().all())

        if not articles:
            return {
                "text": "No articles found in the last 24 hours.",
                "card": None,
            }

        # Top stories (highest importance score)
        top_stories = []
        for article in articles[:5]:
            summary = ""
            if article.summaries:
                summary = article.summaries[0].summary_text

            top_stories.append({
                "title": article.title,
                "url": article.url,
                "summary": summary[:200] if summary else "",
                "importance_score": article.importance_score,
            })

        # Group by category
        categories_map: dict[str, list[dict]] = {}
        for article in articles:
            cat_names = [c.name for c in article.categories] if article.categories else ["Uncategorized"]
            for cat_name in cat_names:
                if cat_name not in categories_map:
                    categories_map[cat_name] = []
                categories_map[cat_name].append({
                    "title": article.title,
                    "url": article.url,
                })

        digest_data = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_count": len(articles),
            "top_stories": top_stories,
            "categories": categories_map,
        }

        card = build_digest_card(digest_data)

        return {
            "text": f"Digest: {len(articles)} article(s) from the last 24 hours.",
            "card": card,
        }

    except Exception:
        logger.exception("Error in /digest command")
        return {
            "text": "Sorry, something went wrong while generating the digest. Please try again.",
            "card": None,
        }
