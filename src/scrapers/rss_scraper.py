"""RSS / Atom feed scraper implementation."""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import feedparser

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

# Shared thread-pool for blocking feedparser calls.
_executor = ThreadPoolExecutor(max_workers=4)

# Default network timeout in seconds.
_DEFAULT_TIMEOUT = 30


def _extract_content(entry: Any) -> str:
    """Return the best available content string from a feed entry.

    Tries, in order: ``content[0].value``, ``summary``, ``description``.
    """
    # 'content' is a list of dicts in Atom feeds
    if hasattr(entry, "content") and entry.content:
        try:
            return entry.content[0].get("value", "")
        except (IndexError, AttributeError, TypeError):
            pass

    if hasattr(entry, "summary") and entry.summary:
        return entry.summary

    if hasattr(entry, "description") and entry.description:
        return entry.description

    return ""


def _parse_published(entry: Any) -> datetime | None:
    """Convert a feed entry's published date to a timezone-aware datetime.

    Falls back through ``published_parsed``, ``updated_parsed``, and raw
    string parsing via :func:`time.mktime`.
    """
    for attr in ("published_parsed", "updated_parsed"):
        time_struct = getattr(entry, attr, None)
        if time_struct is not None:
            try:
                return datetime.fromtimestamp(
                    time.mktime(time_struct), tz=timezone.utc
                )
            except (OverflowError, OSError, ValueError):
                continue

    return None


def _extract_author(entry: Any) -> str | None:
    """Return the author name from a feed entry, or ``None``."""
    author = getattr(entry, "author", None)
    if author:
        return str(author)
    # Some feeds embed author info inside author_detail
    detail = getattr(entry, "author_detail", None)
    if detail:
        return detail.get("name")
    return None


def _extract_tags(entry: Any) -> list[str]:
    """Return tag/category terms from a feed entry."""
    tags = getattr(entry, "tags", None)
    if not tags:
        return []
    return [t.get("term", "") for t in tags if t.get("term")]


class RssScraper(BaseScraper):
    """Scraper for RSS 2.0 and Atom feeds using :mod:`feedparser`."""

    def __init__(self, source_config: dict[str, Any]) -> None:
        super().__init__(source_config)
        self.feed_url: str = source_config["feed_url"]

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch and parse the feed, returning a list of articles.

        The blocking ``feedparser.parse`` call is offloaded to a thread
        executor.  Network and parsing errors are caught so that a broken
        feed never crashes the pipeline.
        """
        loop = asyncio.get_running_loop()

        try:
            feed = await asyncio.wait_for(
                loop.run_in_executor(
                    _executor,
                    feedparser.parse,
                    self.feed_url,
                ),
                timeout=self.config.get("timeout", _DEFAULT_TIMEOUT),
            )
        except asyncio.TimeoutError:
            logger.error(
                "Timeout while fetching feed",
                extra={"feed_url": self.feed_url},
            )
            return []
        except Exception:
            logger.exception(
                "Unexpected error fetching feed",
                extra={"feed_url": self.feed_url},
            )
            return []

        # feedparser sets bozo=1 for any well-formedness issue.
        if getattr(feed, "bozo", False) and not feed.get("entries"):
            exc = getattr(feed, "bozo_exception", None)
            logger.error(
                "Malformed feed with no entries",
                extra={
                    "feed_url": self.feed_url,
                    "bozo_exception": str(exc),
                },
            )
            return []

        feed_title = feed.get("feed", {}).get("title", "")
        source_name = self.config.get("source_name", feed_title or self.feed_url)

        articles: list[RawArticle] = []
        for entry in feed.get("entries", []):
            try:
                title = getattr(entry, "title", None) or ""
                link = getattr(entry, "link", None) or ""
                if not link:
                    # Skip entries with no URL — we cannot deduplicate them.
                    continue

                articles.append(
                    RawArticle(
                        title=title,
                        url=link,
                        raw_content=_extract_content(entry),
                        source_name=source_name,
                        published_at=_parse_published(entry),
                        author=_extract_author(entry),
                        metadata={
                            "tags": _extract_tags(entry),
                            "feed_title": feed_title,
                        },
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to parse feed entry",
                    extra={"feed_url": self.feed_url},
                )
                continue

        logger.info(
            "Parsed %d articles from feed",
            len(articles),
            extra={"feed_url": self.feed_url},
        )
        return articles

    async def close(self) -> None:
        """No-op — feedparser uses no persistent resources."""
