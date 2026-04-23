"""NewsAPI scraper — fetches AI/tech news from NewsAPI.org."""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config.settings import settings
from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

_NEWSAPI_BASE = "https://newsapi.org/v2/everything"

DEFAULT_QUERIES = [
    "artificial intelligence",
    "machine learning",
    "AI startup",
]


class NewsApiScraper(BaseScraper):
    """Scrapes AI/tech news from NewsAPI.org.

    Config keys:
        newsapi_key: API key (falls back to settings.NEWSAPI_KEY).
        queries: List of search terms.
        language: Language filter (default "en").
        page_size: Results per query (default 20, max 100).
    """

    SOURCE_NAME = "newsapi"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        api_key = self.config.get("newsapi_key") or settings.NEWSAPI_KEY
        if not api_key:
            logger.warning("No NewsAPI key configured — NewsApiScraper will return no results")
        self._api_key: str | None = api_key
        self._queries: list[str] = self.config.get("queries", DEFAULT_QUERIES)
        self._language: str = self.config.get("language", "en")
        self._page_size: int = min(self.config.get("page_size", 20), 100)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": "AI-News-Aggregator-Bot/0.1",
                },
                timeout=30.0,
            )
        return self._client

    async def _fetch_query(self, query: str) -> list[RawArticle]:
        """Fetch articles for a single search query."""
        articles: list[RawArticle] = []
        client = await self._get_client()

        params = {
            "q": query,
            "apiKey": self._api_key,
            "language": self._language,
            "sortBy": "publishedAt",
            "pageSize": self._page_size,
        }

        try:
            response = await client.get(_NEWSAPI_BASE, params=params)

            # Handle rate limiting
            if response.status_code == 429:
                logger.warning("NewsAPI rate limit hit for query '%s'", query)
                return articles

            if response.status_code != 200:
                logger.warning(
                    "NewsAPI request failed for '%s': %s %s",
                    query,
                    response.status_code,
                    response.text[:200],
                )
                return articles

            data = response.json()
            if data.get("status") != "ok":
                logger.warning(
                    "NewsAPI returned non-ok status for '%s': %s",
                    query,
                    data.get("message", "unknown error"),
                )
                return articles

            for item in data.get("articles", []):
                title = item.get("title", "")
                url = item.get("url", "")
                if not url or not title:
                    continue

                # Skip "[Removed]" placeholder articles
                if title == "[Removed]":
                    continue

                # Parse published date
                published_at: datetime | None = None
                if item.get("publishedAt"):
                    try:
                        published_at = datetime.fromisoformat(
                            item["publishedAt"].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                # Build content from description + content snippet
                description = item.get("description") or ""
                content_snippet = item.get("content") or ""
                raw_content = description
                if content_snippet and content_snippet != description:
                    raw_content = f"{description}\n\n{content_snippet}"

                source_info = item.get("source", {})

                articles.append(
                    RawArticle(
                        title=title,
                        url=url,
                        raw_content=raw_content,
                        source_name=self.SOURCE_NAME,
                        published_at=published_at,
                        author=item.get("author"),
                        metadata={
                            "source_name": source_info.get("name", ""),
                            "source_id": source_info.get("id", ""),
                            "image_url": item.get("urlToImage"),
                            "search_query": query,
                        },
                    )
                )

        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching NewsAPI for '%s': %s", query, exc)

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch AI/tech news from NewsAPI for all configured queries."""
        if not self._api_key:
            return []

        articles: list[RawArticle] = []
        seen_urls: set[str] = set()

        try:
            for query in self._queries:
                query_articles = await self._fetch_query(query)
                for article in query_articles:
                    if article.url not in seen_urls:
                        seen_urls.add(article.url)
                        articles.append(article)
        except Exception:
            logger.exception("Unexpected error in NewsApiScraper.scrape")

        logger.info(
            "Fetched %d articles from NewsAPI across %d queries",
            len(articles),
            len(self._queries),
        )
        return articles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
