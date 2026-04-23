"""YouTube scraper — fetches AI news from YouTube channels via RSS feeds."""

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

# YouTube RSS feed base URL.
_YT_FEED_URL = "https://www.youtube.com/feeds/videos.xml"

# Well-known AI YouTube channel IDs.
DEFAULT_CHANNEL_IDS = [
    "UCbfYPyITQ-7l4upoX8nvctg",  # Two Minute Papers
    "UCZHmQk67mSJgfCCTn7xBfew",  # Yannic Kilcher
    "UCNJ1Ymd5yFuUPtn21xtRbbw",  # AI Explained
    "UCJMUbFNIijot_iUTGJmVH4Q",  # Matt Wolfe
]


def _parse_published(entry: Any) -> datetime | None:
    """Convert a feed entry's published date to a timezone-aware datetime."""
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


def _extract_thumbnail(entry: Any) -> str | None:
    """Extract thumbnail URL from a YouTube feed entry.

    YouTube RSS feeds include media:group > media:thumbnail elements.
    feedparser exposes these via ``media_thumbnail``.
    """
    thumbs = getattr(entry, "media_thumbnail", None)
    if thumbs:
        for t in thumbs:
            url = t.get("url", "")
            if url:
                return url

    # Fallback: construct from video ID
    link = getattr(entry, "link", "")
    if "watch?v=" in link:
        video_id = link.split("watch?v=")[-1].split("&")[0]
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    # yt:videoId element
    video_id = getattr(entry, "yt_videoid", None)
    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    return None


def _extract_channel_name(feed: Any) -> str:
    """Extract the channel name from the feed metadata."""
    feed_meta = feed.get("feed", {})
    title = feed_meta.get("title", "")
    if title:
        return title
    author = feed_meta.get("author", "")
    return author or "YouTube"


class YouTubeScraper(BaseScraper):
    """Scrapes AI news from YouTube channels via RSS feeds.

    Config keys:
        channel_ids: List of YouTube channel IDs.
    """

    SOURCE_NAME = "youtube"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._channel_ids: list[str] = self.config.get("channel_ids", DEFAULT_CHANNEL_IDS)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_channel(self, channel_id: str) -> list[RawArticle]:
        """Fetch and parse the RSS feed for a single YouTube channel."""
        articles: list[RawArticle] = []
        feed_url = f"{_YT_FEED_URL}?channel_id={channel_id}"
        loop = asyncio.get_running_loop()

        try:
            feed = await asyncio.wait_for(
                loop.run_in_executor(_executor, feedparser.parse, feed_url),
                timeout=self.config.get("timeout", _DEFAULT_TIMEOUT),
            )
        except asyncio.TimeoutError:
            logger.error("Timeout fetching YouTube feed for channel %s", channel_id)
            return articles
        except Exception:
            logger.exception("Error fetching YouTube feed for channel %s", channel_id)
            return articles

        if getattr(feed, "bozo", False) and not feed.get("entries"):
            logger.error(
                "Malformed YouTube feed for channel %s: %s",
                channel_id,
                getattr(feed, "bozo_exception", "unknown"),
            )
            return articles

        channel_name = _extract_channel_name(feed)

        for entry in feed.get("entries", []):
            try:
                title = getattr(entry, "title", None) or ""
                link = getattr(entry, "link", None) or ""
                if not link:
                    continue

                # YouTube feed entries have a summary/description
                description = ""
                if hasattr(entry, "summary") and entry.summary:
                    description = entry.summary
                elif hasattr(entry, "media_group") and entry.media_group:
                    # Some feeds use media:group > media:description
                    for mg in entry.media_group:
                        desc = mg.get("content", "")
                        if desc:
                            description = desc
                            break

                thumbnail = _extract_thumbnail(entry)
                video_id = getattr(entry, "yt_videoid", None)
                if not video_id and "watch?v=" in link:
                    video_id = link.split("watch?v=")[-1].split("&")[0]

                articles.append(
                    RawArticle(
                        title=f"[YouTube] {channel_name}: {title}",
                        url=link,
                        raw_content=description or title,
                        source_name=self.SOURCE_NAME,
                        published_at=_parse_published(entry),
                        author=channel_name,
                        metadata={
                            "video_id": video_id,
                            "video_url": link,
                            "thumbnail_url": thumbnail,
                            "image_url": thumbnail,
                            "channel_name": channel_name,
                            "channel_id": channel_id,
                        },
                    )
                )
            except Exception:
                logger.exception(
                    "Failed to parse YouTube feed entry for channel %s", channel_id
                )
                continue

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch latest videos from all configured YouTube channels."""
        articles: list[RawArticle] = []
        try:
            for channel_id in self._channel_ids:
                channel_articles = await self._fetch_channel(channel_id)
                articles.extend(channel_articles)
        except Exception:
            logger.exception("Unexpected error in YouTubeScraper.scrape")

        logger.info(
            "Fetched %d videos from %d YouTube channels",
            len(articles),
            len(self._channel_ids),
        )
        return articles

    async def close(self) -> None:
        """No-op — feedparser uses no persistent resources."""
