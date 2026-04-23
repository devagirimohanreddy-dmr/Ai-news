"""LinkedIn scraper — fetches AI company news via Google News RSS (LinkedIn requires auth).

Since LinkedIn blocks unauthenticated access, this scraper uses Google News RSS
to find recent news *about* the configured companies. This gives us the same
company intelligence without needing LinkedIn credentials.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
import time as _time

import feedparser

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2)

# Google News RSS endpoint for search queries
_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class LinkedInScraper(BaseScraper):
    """Fetches company AI news via Google News RSS as a proxy for LinkedIn.

    Config keys:
        company_names: List of company names to search for (e.g. ["OpenAI", "Anthropic"])
        company_urls: Legacy — extracts company name from URL path.
    """

    SOURCE_NAME = "linkedin"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        # Support both company_names (preferred) and company_urls (legacy)
        self._company_names: list[str] = self.config.get("company_names", [])
        if not self._company_names:
            # Extract names from URLs: https://linkedin.com/company/openai -> "OpenAI"
            for url in self.config.get("company_urls", []):
                name = url.rstrip("/").split("/")[-1].replace("-", " ").title()
                self._company_names.append(name)

    async def scrape(self) -> list[RawArticle]:
        if not self._company_names:
            logger.warning("No company names configured for LinkedInScraper")
            return []

        articles: list[RawArticle] = []
        seen_urls: set[str] = set()
        loop = asyncio.get_running_loop()

        for company in self._company_names:
            query = f"{company} AI"
            feed_url = _GOOGLE_NEWS_RSS.format(query=query.replace(" ", "+"))

            try:
                feed = await asyncio.wait_for(
                    loop.run_in_executor(_executor, feedparser.parse, feed_url),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.error("Timeout fetching Google News for %s", company)
                continue
            except Exception:
                logger.exception("Error fetching Google News for %s", company)
                continue

            for entry in feed.get("entries", [])[:10]:
                link = getattr(entry, "link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                title = getattr(entry, "title", "")
                content = getattr(entry, "summary", "") or getattr(entry, "description", "")

                # Parse published date
                published_at = None
                for attr in ("published_parsed", "updated_parsed"):
                    ts = getattr(entry, attr, None)
                    if ts:
                        try:
                            published_at = datetime.fromtimestamp(
                                _time.mktime(ts), tz=timezone.utc
                            )
                        except (OverflowError, OSError, ValueError):
                            pass
                        break

                source_name = getattr(entry, "source", {})
                if hasattr(source_name, "get"):
                    source_name = source_name.get("title", "Google News")
                else:
                    source_name = "Google News"

                articles.append(
                    RawArticle(
                        title=f"[{company}] {title}",
                        url=link,
                        raw_content=content,
                        source_name=self.SOURCE_NAME,
                        published_at=published_at,
                        author=source_name,
                        metadata={
                            "company_name": company,
                            "original_source": source_name,
                        },
                    )
                )

        logger.info(
            "Fetched %d articles about %d companies via Google News",
            len(articles), len(self._company_names),
        )
        return articles

    async def close(self) -> None:
        pass
