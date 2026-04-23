"""Hacker News scraper — fetches top/new/best stories via the Firebase API."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"
BATCH_SIZE = 10
MIN_SCORE = 50
VALID_STORY_TYPES = {"top", "new", "best"}


class HackerNewsScraper(BaseScraper):
    """Scrape stories from Hacker News.

    Config keys:
        story_type: One of "top", "new", "best" (default "top").
        limit: Maximum number of stories to return (default 30).
    """

    SOURCE_NAME = "hackernews"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        story_type = self.config.get("story_type", "top")
        if story_type not in VALID_STORY_TYPES:
            logger.warning("Invalid story_type '%s', falling back to 'top'", story_type)
            story_type = "top"
        self._story_type: str = story_type
        self._limit: int = self.config.get("limit", 30)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": "AI-News-Aggregator-Bot/0.1"},
                timeout=30.0,
            )
        return self._client

    async def _fetch_item(self, client: httpx.AsyncClient, item_id: int) -> dict[str, Any] | None:
        """Fetch a single HN item by ID."""
        url = f"{HN_BASE}/item/{item_id}.json"
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
            logger.warning("HN item %d returned status %d", item_id, response.status_code)
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching HN item %d: %s", item_id, exc)
        return None

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    async def _fetch_story_ids(self) -> list[int]:
        client = await self._get_client()
        url = f"{HN_BASE}/{self._story_type}stories.json"
        try:
            response = await client.get(url)
            if response.status_code != 200:
                logger.warning("HN story IDs request failed: %s", response.status_code)
                return []
            ids: list[int] = response.json()
            return ids[: self._limit]
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching HN story IDs: %s", exc)
            return []

    async def _fetch_stories(self, story_ids: list[int]) -> list[RawArticle]:
        articles: list[RawArticle] = []
        client = await self._get_client()

        # Fetch in batches to avoid overwhelming the API
        for i in range(0, len(story_ids), BATCH_SIZE):
            batch = story_ids[i : i + BATCH_SIZE]
            results = await asyncio.gather(
                *(self._fetch_item(client, sid) for sid in batch),
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.error("Exception fetching HN story: %s", result)
                    continue
                if result is None:
                    continue

                score = result.get("score", 0)
                if score <= MIN_SCORE:
                    continue

                published = None
                if result.get("time"):
                    published = datetime.fromtimestamp(result["time"], tz=timezone.utc)

                story_url = result.get("url", "")
                if not story_url:
                    story_url = f"https://news.ycombinator.com/item?id={result.get('id', '')}"

                articles.append(
                    RawArticle(
                        title=result.get("title", ""),
                        url=story_url,
                        raw_content=result.get("text", "") or "",
                        source_name=self.SOURCE_NAME,
                        published_at=published,
                        author=result.get("by"),
                        metadata={
                            "hn_id": result.get("id"),
                            "score": score,
                            "descendants": result.get("descendants", 0),
                            "type": result.get("type", "story"),
                        },
                    )
                )

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch top/new/best HN stories above the score threshold."""
        try:
            story_ids = await self._fetch_story_ids()
            if not story_ids:
                return []
            return await self._fetch_stories(story_ids)
        except Exception:
            logger.exception("Unexpected error in HackerNewsScraper.scrape")
            return []

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
