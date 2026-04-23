"""Handler for the /summarize command."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.summary import Summary
from src.bot.cards.article_card import build_article_card

logger = logging.getLogger(__name__)

_URL_RE = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)


def _validate_url(url: str) -> str | None:
    """Validate and normalise a URL. Returns cleaned URL or None."""
    url = url.strip().rstrip("/")
    if not _URL_RE.fullmatch(url):
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return url


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


async def handle_summarize(
    session: AsyncSession,
    args: str,
    pipeline_factory: Any = None,
) -> dict[str, Any]:
    """Summarize an article by URL.

    If the article is already in the database, returns the cached summary.
    Otherwise, scrapes the URL, runs it through the pipeline, and returns
    the new summary.

    Args:
        session: Async SQLAlchemy session.
        args: The URL to summarize.
        pipeline_factory: Callable that returns an ``ArticlePipeline`` instance
            bound to the given session.  Signature:
            ``pipeline_factory(session) -> ArticlePipeline``.

    Returns:
        A dict with ``"card"`` (Adaptive Card dict), ``"text"`` (fallback),
        and ``"show_typing"`` (bool).
    """
    try:
        raw_url = args.strip() if args else ""
        if not raw_url:
            return {
                "text": "Please provide a URL. Usage: `/summarize [URL]`",
                "card": None,
                "show_typing": False,
            }

        url = _validate_url(raw_url)
        if url is None:
            return {
                "text": f"'{raw_url}' does not look like a valid URL. "
                        f"Please provide a full URL starting with http:// or https://.",
                "card": None,
                "show_typing": False,
            }

        # Check if article already exists
        url_h = _url_hash(url)
        stmt = select(Article).where(Article.url_hash == url_h)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing and existing.summaries:
            # Return cached summary
            summary_obj = existing.summaries[0]
            categories = [c.name for c in existing.categories] if existing.categories else []
            source_name = existing.source.name if existing.source else "User submitted"

            data = {
                "title": existing.title,
                "url": existing.url,
                "summary": summary_obj.summary_text,
                "headline": summary_obj.headline,
                "categories": categories,
                "source_name": source_name,
                "importance_score": existing.importance_score,
                "published_at": existing.published_at.isoformat() if existing.published_at else None,
                "author": existing.author,
            }
            card = build_article_card(data)
            return {
                "text": "Here is the cached summary:",
                "card": card,
                "show_typing": False,
            }

        # Article not in DB (or no summary) — process through pipeline
        if pipeline_factory is None:
            return {
                "text": "The article pipeline is not available right now. "
                        "Please try again later.",
                "card": None,
                "show_typing": False,
            }

        # Signal to caller to show typing indicator
        # The actual processing happens below
        from src.scrapers.base import RawArticle

        raw_article = RawArticle(
            title=f"User-submitted: {url}",
            url=url,
            raw_content="",  # Pipeline ingest will scrape the content
            source_name="user_submitted",
        )

        pipeline = pipeline_factory(session)
        article = await pipeline.process(raw_article)

        if article is None:
            return {
                "text": "Failed to process the article. It may be a duplicate or the URL could not be scraped.",
                "card": None,
                "show_typing": True,
            }

        # Build card from the processed article
        summary_text = ""
        headline = ""
        if article.summaries:
            summary_text = article.summaries[0].summary_text
            headline = article.summaries[0].headline or ""

        categories = [c.name for c in article.categories] if article.categories else []
        source_name = article.source.name if article.source else "User submitted"

        data = {
            "title": article.title,
            "url": article.url,
            "summary": summary_text,
            "headline": headline,
            "categories": categories,
            "source_name": source_name,
            "importance_score": article.importance_score,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "author": article.author,
        }
        card = build_article_card(data)
        return {
            "text": "Here is the summary:",
            "card": card,
            "show_typing": True,
        }

    except Exception:
        logger.exception("Error in /summarize command")
        return {
            "text": "Sorry, something went wrong while summarizing the article. Please try again.",
            "card": None,
            "show_typing": False,
        }
