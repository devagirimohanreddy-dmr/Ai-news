"""Handler for the /search command."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text as sa_text, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.bot.cards.article_card import build_article_card

logger = logging.getLogger(__name__)

MAX_RESULTS = 10

# Allow only alphanumeric, spaces, hyphens, and common punctuation
_SAFE_QUERY_RE = re.compile(r"[^\w\s\-.,!?']", re.UNICODE)


def _sanitize_query(query: str) -> str:
    """Remove potentially dangerous characters from search input."""
    sanitized = _SAFE_QUERY_RE.sub("", query)
    return sanitized.strip()[:200]  # Limit length


async def handle_search(session: AsyncSession, args: str) -> dict[str, Any]:
    """Full-text search across article markdown_content.

    Uses PostgreSQL ``to_tsvector`` / ``plainto_tsquery`` for ranked results.

    Args:
        session: Async SQLAlchemy session.
        args: The search query string.

    Returns:
        A dict with ``"cards"`` (list of Adaptive Card dicts) and
        ``"text"`` (fallback text).
    """
    try:
        raw_query = args.strip() if args else ""
        if not raw_query:
            return {
                "text": "Please provide a search query. Usage: `/search [query]`",
                "cards": [],
            }

        query = _sanitize_query(raw_query)
        if not query:
            return {
                "text": "Invalid search query. Please use alphanumeric characters.",
                "cards": [],
            }

        # PostgreSQL full-text search with relevance ranking
        sql = sa_text("""
            SELECT
                a.id,
                a.title,
                a.url,
                a.author,
                a.published_at,
                a.importance_score,
                a.pipeline_status,
                ts_rank(to_tsvector('english', COALESCE(a.markdown_content, '')),
                        plainto_tsquery('english', :query)) AS rank
            FROM articles a
            WHERE to_tsvector('english', COALESCE(a.markdown_content, ''))
                  @@ plainto_tsquery('english', :query)
            ORDER BY rank DESC
            LIMIT :limit
        """)

        result = await session.execute(sql, {"query": query, "limit": MAX_RESULTS})
        rows = result.fetchall()

        if not rows:
            return {
                "text": f"No articles found matching '{raw_query}'.",
                "cards": [],
            }

        # Fetch full Article objects for the matched IDs to get relationships
        article_ids = [row.id for row in rows]
        stmt = select(Article).where(Article.id.in_(article_ids))
        full_result = await session.execute(stmt)
        articles_map = {a.id: a for a in full_result.scalars().unique().all()}

        cards = []
        for row in rows:
            article = articles_map.get(row.id)
            if article is None:
                continue

            categories = [c.name for c in article.categories] if article.categories else []
            summary = ""
            if article.summaries:
                summary = article.summaries[0].summary_text

            source_name = article.source.name if article.source else "User submitted"

            data = {
                "title": article.title,
                "url": article.url,
                "summary": summary,
                "categories": categories,
                "source_name": source_name,
                "importance_score": article.importance_score,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "author": article.author,
            }
            cards.append(build_article_card(data))

        return {
            "text": f"Found {len(cards)} result(s) for '{raw_query}':",
            "cards": cards,
        }

    except Exception:
        logger.exception("Error in /search command")
        return {
            "text": "Sorry, something went wrong while searching. Please try again.",
            "cards": [],
        }
