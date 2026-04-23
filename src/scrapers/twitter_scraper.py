"""Twitter/X scraper — fetches AI news tweets via Tweepy (Twitter API v2)."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import tweepy

from src.config.settings import settings
from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_QUERIES = [
    "AI news",
    "machine learning",
    "LLM",
    "GPT",
    "Claude AI",
]
DEFAULT_MAX_RESULTS = 50


class TwitterScraper(BaseScraper):
    """Scrapes AI news from X/Twitter using Tweepy (Twitter API v2).

    Config keys:
        twitter_bearer_token: Bearer token for Twitter API v2 (falls back to settings).
        search_queries: List of search query strings.
        max_results: Max tweets per query (10-100, default 50).
    """

    SOURCE_NAME = "twitter"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        bearer_token = self.config.get("twitter_bearer_token") or settings.TWITTER_BEARER_TOKEN
        if not bearer_token:
            logger.warning("No Twitter bearer token configured — TwitterScraper will return no results")
        self._bearer_token: str | None = bearer_token
        self._queries: list[str] = self.config.get("search_queries", DEFAULT_SEARCH_QUERIES)
        self._max_results: int = min(max(self.config.get("max_results", DEFAULT_MAX_RESULTS), 10), 100)
        self._client: tweepy.Client | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> tweepy.Client:
        if self._client is None:
            self._client = tweepy.Client(
                bearer_token=self._bearer_token,
                wait_on_rate_limit=False,
            )
        return self._client

    @staticmethod
    def _tweet_url(author_id: str | None, tweet_id: str) -> str:
        """Build the canonical URL for a tweet."""
        if author_id:
            return f"https://twitter.com/i/web/status/{tweet_id}"
        return f"https://twitter.com/i/web/status/{tweet_id}"

    def _search_query(self, query: str) -> tweepy.Response | None:
        """Execute a single search (blocking — run via asyncio.to_thread)."""
        client = self._get_client()
        try:
            response = client.search_recent_tweets(
                query=f"{query} -is:retweet lang:en",
                max_results=self._max_results,
                tweet_fields=["created_at", "public_metrics", "author_id", "attachments"],
                expansions=["author_id", "attachments.media_keys"],
                media_fields=["url", "preview_image_url", "type"],
                user_fields=["username", "name"],
            )
            return response
        except tweepy.TooManyRequests:
            logger.warning("Twitter rate limit hit for query '%s'", query)
            return None
        except tweepy.TweepyException as exc:
            logger.error("Tweepy error for query '%s': %s", query, exc)
            return None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def _scrape_via_google_news(self) -> list[RawArticle]:
        """Fallback: search Google News RSS for Twitter/AI topics when no API key."""
        import feedparser
        import time as _time

        articles: list[RawArticle] = []
        seen_urls: set[str] = set()
        loop = asyncio.get_running_loop()

        for query in self._queries[:3]:  # limit queries for fallback
            feed_url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}+site:x.com+OR+site:twitter.com&hl=en-US&gl=US&ceid=US:en"
            try:
                feed = await asyncio.wait_for(
                    loop.run_in_executor(None, feedparser.parse, feed_url),
                    timeout=30,
                )
            except Exception:
                continue

            for entry in feed.get("entries", [])[:10]:
                link = getattr(entry, "link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                title = getattr(entry, "title", "")
                content = getattr(entry, "summary", "")
                published_at = None
                for attr in ("published_parsed", "updated_parsed"):
                    ts = getattr(entry, attr, None)
                    if ts:
                        try:
                            published_at = datetime.fromtimestamp(_time.mktime(ts), tz=timezone.utc)
                        except Exception:
                            pass
                        break

                articles.append(
                    RawArticle(
                        title=title,
                        url=link,
                        raw_content=content,
                        source_name=self.SOURCE_NAME,
                        published_at=published_at,
                        author="Google News (Twitter fallback)",
                        metadata={"search_query": query, "fallback": True},
                    )
                )

        logger.info("Fallback: fetched %d articles via Google News", len(articles))
        return articles

    async def scrape(self) -> list[RawArticle]:
        """Fetch recent tweets matching configured search queries."""
        if not self._bearer_token:
            logger.info("No Twitter API key — using Google News fallback")
            return await self._scrape_via_google_news()

        articles: list[RawArticle] = []
        seen_ids: set[str] = set()

        try:
            for query in self._queries:
                try:
                    response = await asyncio.to_thread(self._search_query, query)
                except Exception as exc:
                    logger.error("Error searching Twitter for '%s': %s", query, exc)
                    continue

                if response is None or response.data is None:
                    continue

                # Build lookup maps for users and media
                users: dict[str, Any] = {}
                media_map: dict[str, Any] = {}
                if response.includes:
                    for user in response.includes.get("users", []):
                        users[str(user.id)] = user
                    for m in response.includes.get("media", []):
                        media_map[m.media_key] = m

                for tweet in response.data:
                    tweet_id = str(tweet.id)
                    if tweet_id in seen_ids:
                        continue
                    seen_ids.add(tweet_id)

                    # Resolve author
                    author_id = str(tweet.author_id) if tweet.author_id else None
                    author_user = users.get(author_id) if author_id else None
                    author_name = author_user.username if author_user else None
                    author_display = author_user.name if author_user else None

                    # Resolve media / image
                    image_url: str | None = None
                    if tweet.attachments and tweet.attachments.get("media_keys"):
                        for mk in tweet.attachments["media_keys"]:
                            m = media_map.get(mk)
                            if m:
                                image_url = getattr(m, "url", None) or getattr(m, "preview_image_url", None)
                                if image_url:
                                    break

                    # Parse metrics
                    metrics = tweet.public_metrics or {}

                    # Published date
                    published_at: datetime | None = None
                    if tweet.created_at:
                        if isinstance(tweet.created_at, datetime):
                            published_at = tweet.created_at.replace(tzinfo=timezone.utc) if tweet.created_at.tzinfo is None else tweet.created_at
                        else:
                            try:
                                published_at = datetime.fromisoformat(str(tweet.created_at).replace("Z", "+00:00"))
                            except (ValueError, TypeError):
                                pass

                    tweet_url = self._tweet_url(author_id, tweet_id)

                    articles.append(
                        RawArticle(
                            title=f"@{author_name}: {tweet.text[:80]}..." if author_name and len(tweet.text) > 80 else f"@{author_name}: {tweet.text}" if author_name else tweet.text[:120],
                            url=tweet_url,
                            raw_content=tweet.text,
                            source_name=self.SOURCE_NAME,
                            published_at=published_at,
                            author=author_display or author_name,
                            metadata={
                                "tweet_id": tweet_id,
                                "retweet_count": metrics.get("retweet_count", 0),
                                "like_count": metrics.get("like_count", 0),
                                "reply_count": metrics.get("reply_count", 0),
                                "image_url": image_url,
                                "search_query": query,
                            },
                        )
                    )
        except Exception:
            logger.exception("Unexpected error in TwitterScraper.scrape")

        logger.info("Fetched %d tweets across %d queries", len(articles), len(self._queries))
        return articles

    async def close(self) -> None:
        """No-op — tweepy.Client uses no persistent resources."""
        self._client = None
