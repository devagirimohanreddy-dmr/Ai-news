"""Telegram scraper — fetches AI news from public Telegram channels via web preview."""

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

# Regex patterns for parsing Telegram's public web preview pages.
# The web preview at https://t.me/s/{channel} renders recent posts as HTML.
_MESSAGE_BLOCK_RE = re.compile(
    r'<div class="tgme_widget_message_wrap[^"]*"[^>]*>.*?'
    r'<div class="tgme_widget_message_bubble[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_MESSAGE_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
_MESSAGE_DATE_RE = re.compile(
    r'<time[^>]+datetime="([^"]+)"',
)
_MESSAGE_LINK_RE = re.compile(
    r'data-post="([^"]+)"',
)
_MESSAGE_IMAGE_RE = re.compile(
    r"background-image:\s*url\('([^']+)'\)",
)
_MESSAGE_PHOTO_RE = re.compile(
    r'<a class="tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:\s*url\(\'([^\']+)\'\)',
    re.DOTALL,
)
_CHANNEL_TITLE_RE = re.compile(
    r'<div class="tgme_channel_info_header_title[^"]*"[^>]*>\s*<span[^>]*>(.*?)</span>',
    re.DOTALL,
)


def _clean_html(text: str) -> str:
    """Strip HTML tags, decode entities, and collapse whitespace."""
    # Replace <br> with newlines
    cleaned = re.sub(r"<br\s*/?>", "\n", text)
    # Strip remaining tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Decode common HTML entities
    cleaned = cleaned.replace("&amp;", "&")
    cleaned = cleaned.replace("&lt;", "<")
    cleaned = cleaned.replace("&gt;", ">")
    cleaned = cleaned.replace("&quot;", '"')
    cleaned = cleaned.replace("&#39;", "'")
    # Collapse whitespace
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class TelegramScraper(BaseScraper):
    """Scrapes AI news from public Telegram channels via web preview.

    Public Telegram channels have a web preview at ``https://t.me/s/{channel_name}``.
    This scraper fetches the HTML and parses post content without authentication.

    Config keys:
        channels: List of channel usernames (without @).
    """

    SOURCE_NAME = "telegram"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._channels: list[str] = self.config.get("channels", [])
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def _fetch_channel(self, channel: str) -> list[RawArticle]:
        """Fetch and parse posts from a single Telegram channel."""
        articles: list[RawArticle] = []
        client = await self._get_client()
        url = f"https://t.me/s/{channel}"

        try:
            response = await client.get(url)

            if response.status_code != 200:
                logger.warning(
                    "Telegram request failed for @%s: %s", channel, response.status_code
                )
                return articles

            html = response.text

            # Try to extract channel title
            title_match = _CHANNEL_TITLE_RE.search(html)
            channel_title = _clean_html(title_match.group(1)) if title_match else channel

            # Extract message texts
            message_texts = _MESSAGE_TEXT_RE.findall(html)
            message_dates = _MESSAGE_DATE_RE.findall(html)
            message_links = _MESSAGE_LINK_RE.findall(html)
            message_images = _MESSAGE_PHOTO_RE.findall(html)
            if not message_images:
                message_images = _MESSAGE_IMAGE_RE.findall(html)

            for i, raw_text in enumerate(message_texts):
                text = _clean_html(raw_text)
                if not text or len(text) < 10:
                    continue

                # Build post URL
                if i < len(message_links):
                    post_id = message_links[i]
                    post_url = f"https://t.me/{post_id}"
                else:
                    post_url = f"https://t.me/{channel}"

                # Parse date
                published_at: datetime | None = None
                if i < len(message_dates):
                    try:
                        published_at = datetime.fromisoformat(
                            message_dates[i].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                # Image
                image_url = message_images[i] if i < len(message_images) else None

                # Title: first line or first 80 chars
                first_line = text.split("\n")[0][:80]
                title = first_line
                if len(first_line) == 80:
                    title += "..."

                articles.append(
                    RawArticle(
                        title=f"[Telegram] {channel_title}: {title}",
                        url=post_url,
                        raw_content=text,
                        source_name=self.SOURCE_NAME,
                        published_at=published_at,
                        author=channel_title,
                        metadata={
                            "channel": channel,
                            "channel_title": channel_title,
                            "image_url": image_url,
                        },
                    )
                )

        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching Telegram @%s: %s", channel, exc)
        except Exception:
            logger.exception("Unexpected error parsing Telegram channel @%s", channel)

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch posts from all configured Telegram channels."""
        articles: list[RawArticle] = []
        try:
            for channel in self._channels:
                channel_articles = await self._fetch_channel(channel)
                articles.extend(channel_articles)
        except Exception:
            logger.exception("Unexpected error in TelegramScraper.scrape")

        logger.info(
            "Fetched %d posts from %d Telegram channels",
            len(articles),
            len(self._channels),
        )
        return articles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
