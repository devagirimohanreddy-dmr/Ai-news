"""Reddit scraper — fetches hot posts from AI/tech subreddits via the JSON API."""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

DEFAULT_SUBREDDITS = ["MachineLearning", "artificial", "technology"]
MIN_SCORE = 50


class RedditScraper(BaseScraper):
    """Scrape hot posts from configurable subreddits.

    Config keys:
        subreddits: List of subreddit names (default: MachineLearning, artificial, technology).
        limit: Number of posts per subreddit (default 25).
    """

    SOURCE_NAME = "reddit"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._subreddits: list[str] = self.config.get("subreddits", DEFAULT_SUBREDDITS)
        self._limit: int = self.config.get("limit", 25)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": "AI-News-Aggregator-Bot/0.1 (by /u/ainews_aggregator)",
                },
                timeout=30.0,
            )
        return self._client

    @staticmethod
    def _should_include(post_data: dict[str, Any]) -> bool:
        """Filter posts: high score OR substantial self-text."""
        score = post_data.get("score", 0)
        if score > MIN_SCORE:
            return True
        if post_data.get("is_self") and len(post_data.get("selftext", "")) > 200:
            return True
        return False

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    async def _fetch_subreddit(self, subreddit: str) -> list[RawArticle]:
        articles: list[RawArticle] = []
        client = await self._get_client()

        url = f"https://www.reddit.com/r/{subreddit}/hot.json"
        params = {"limit": self._limit}

        try:
            response = await client.get(url, params=params)

            # Handle rate limiting with simple backoff
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                logger.warning(
                    "Reddit rate limited on r/%s — retrying after %ds",
                    subreddit,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.warning(
                    "Reddit request failed for r/%s: %s", subreddit, response.status_code
                )
                return articles

            data = response.json()
            children = data.get("data", {}).get("children", [])

            for child in children:
                post = child.get("data", {})
                if not self._should_include(post):
                    continue

                created = None
                if post.get("created_utc"):
                    created = datetime.fromtimestamp(
                        post["created_utc"], tz=timezone.utc
                    )

                post_url = post.get("url", "")
                if post.get("is_self"):
                    post_url = f"https://www.reddit.com{post.get('permalink', '')}"

                content = post.get("selftext", "") or post.get("url", "")

                articles.append(
                    RawArticle(
                        title=post.get("title", ""),
                        url=post_url,
                        raw_content=content,
                        source_name=self.SOURCE_NAME,
                        published_at=created,
                        author=post.get("author"),
                        metadata={
                            "subreddit": subreddit,
                            "score": post.get("score", 0),
                            "num_comments": post.get("num_comments", 0),
                            "is_self": post.get("is_self", False),
                            "permalink": post.get("permalink", ""),
                        },
                    )
                )
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching r/%s: %s", subreddit, exc)

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch hot posts from configured subreddits."""
        articles: list[RawArticle] = []
        try:
            for subreddit in self._subreddits:
                sub_articles = await self._fetch_subreddit(subreddit)
                articles.extend(sub_articles)
        except Exception:
            logger.exception("Unexpected error in RedditScraper.scrape")
        return articles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
