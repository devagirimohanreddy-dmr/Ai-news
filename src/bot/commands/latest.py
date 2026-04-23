"""Handler for the /latest command."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.article import Article
from src.models.category import Category
from src.bot.cards.article_card import build_article_card

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


async def handle_latest(session: AsyncSession, args: str) -> dict[str, Any]:
    """Return the most recent articles, optionally filtered by category.

    Args:
        session: Async SQLAlchemy session.
        args: Optional category name for filtering.

    Returns:
        A dict with ``"cards"`` (list of Adaptive Card dicts) and
        ``"text"`` (fallback text).
    """
    try:
        category_filter = args.strip() if args else None

        stmt = (
            select(Article)
            .where(Article.pipeline_status == "routed")
            .order_by(Article.created_at.desc())
            .limit(MAX_RESULTS)
        )

        # Fuzzy match on category name using ILIKE
        if category_filter:
            stmt = (
                select(Article)
                .join(Article.categories)
                .where(Article.pipeline_status == "routed")
                .where(func.lower(Category.name).contains(category_filter.lower()))
                .order_by(Article.created_at.desc())
                .limit(MAX_RESULTS)
            )

        result = await session.execute(stmt)
        articles = result.scalars().unique().all()

        if not articles:
            filter_msg = f" in category '{category_filter}'" if category_filter else ""
            return {
                "text": f"No recent articles found{filter_msg}.",
                "cards": [],
            }

        cards = []
        for article in articles:
            data = _article_to_card_data(article)
            cards.append(build_article_card(data))

        filter_label = f" ({category_filter})" if category_filter else ""
        return {
            "text": f"Latest {len(cards)} article(s){filter_label}:",
            "cards": cards,
        }

    except Exception:
        logger.exception("Error in /latest command")
        return {
            "text": "Sorry, something went wrong while fetching the latest articles. Please try again.",
            "cards": [],
        }


def _article_to_card_data(article: Article) -> dict[str, Any]:
    """Convert an Article ORM instance to a dict suitable for card building."""
    categories = [c.name for c in article.categories] if article.categories else []
    summary = ""
    if article.summaries:
        summary = article.summaries[0].summary_text

    source_name = article.source.name if article.source else "User submitted"

    return {
        "title": article.title,
        "url": article.url,
        "summary": summary,
        "categories": categories,
        "source_name": source_name,
        "importance_score": article.importance_score,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "author": article.author,
    }
