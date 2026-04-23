"""Firecrawl scraper — extracts article content via a self-hosted Firecrawl instance."""

import asyncio
import logging
from typing import Any

import httpx

from src.config.settings import settings
from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

# Network timeout for each Firecrawl request (seconds).
_REQUEST_TIMEOUT = 60.0

# Delay before a single retry on failure (seconds).
_RETRY_DELAY = 2.0


class FirecrawlScraper(BaseScraper):
    """Scrape web pages via a self-hosted Firecrawl instance.

    Firecrawl converts web pages to clean markdown, making it ideal for
    extracting article content from arbitrary URLs.

    Config keys:
        urls: List of URLs to scrape.
    """

    SOURCE_NAME = "firecrawl"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._urls: list[str] = self.config.get("urls", [])
        self._base_url: str = settings.FIRECRAWL_BASE_URL
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AI-News-Aggregator-Bot/0.1",
                },
            )
        return self._client

    async def _scrape_url(self, url: str) -> RawArticle | None:
        """Scrape a single URL via the Firecrawl API.

        Retries once on failure after a short delay.
        """
        client = await self._get_client()
        endpoint = f"{self._base_url}/v1/scrape"
        payload = {"url": url, "formats": ["markdown"]}

        for attempt in range(2):
            try:
                response = await client.post(endpoint, json=payload)

                if response.status_code != 200:
                    logger.warning(
                        "Firecrawl returned %s for %s (attempt %d)",
                        response.status_code,
                        url,
                        attempt + 1,
                    )
                    if attempt == 0:
                        await asyncio.sleep(_RETRY_DELAY)
                        continue
                    return None

                data = response.json()

                if not data.get("success", False):
                    error_msg = data.get("error", "unknown error")
                    logger.warning(
                        "Firecrawl error for %s: %s (attempt %d)",
                        url,
                        error_msg,
                        attempt + 1,
                    )
                    if attempt == 0:
                        await asyncio.sleep(_RETRY_DELAY)
                        continue
                    return None

                result = data.get("data", {})
                markdown = result.get("markdown", "")
                metadata = result.get("metadata", {})
                title = metadata.get("title", "") or result.get("title", "")

                return RawArticle(
                    title=title,
                    url=url,
                    raw_content=markdown,
                    source_name=self.SOURCE_NAME,
                    published_at=None,
                    author=metadata.get("author"),
                    metadata={
                        "description": metadata.get("description", ""),
                        "language": metadata.get("language", ""),
                        "source_url": metadata.get("sourceURL", url),
                        "scraper": "firecrawl",
                    },
                )

            except httpx.ConnectError as exc:
                logger.error(
                    "Cannot connect to Firecrawl at %s for %s (attempt %d): %s",
                    self._base_url,
                    url,
                    attempt + 1,
                    exc,
                )
                if attempt == 0:
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                return None

            except httpx.TimeoutException:
                logger.error(
                    "Timeout scraping %s via Firecrawl (attempt %d)",
                    url,
                    attempt + 1,
                )
                if attempt == 0:
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                return None

            except Exception:
                logger.exception(
                    "Unexpected error scraping %s via Firecrawl (attempt %d)",
                    url,
                    attempt + 1,
                )
                if attempt == 0:
                    await asyncio.sleep(_RETRY_DELAY)
                    continue
                return None

        return None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Scrape all configured URLs via Firecrawl and return articles."""
        articles: list[RawArticle] = []
        try:
            for url in self._urls:
                article = await self._scrape_url(url)
                if article is not None:
                    articles.append(article)
        except Exception:
            logger.exception("Unexpected error in FirecrawlScraper.scrape")

        logger.info("Firecrawl scraped %d articles from %d URLs", len(articles), len(self._urls))
        return articles

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
