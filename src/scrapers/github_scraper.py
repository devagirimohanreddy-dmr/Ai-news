"""GitHub scraper — fetches releases from tracked repos and trending AI repositories."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)


class GitHubScraper(BaseScraper):
    """Scrape GitHub releases and trending AI repositories.

    Config keys:
        github_token: Optional personal-access token for higher rate limits.
        repos: List of "owner/repo" strings whose releases to track.
        trending_language: Language filter for trending search (default "python").
    """

    SOURCE_NAME = "github"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._token: str | None = self.config.get("github_token")
        self._repos: list[str] = self.config.get("repos", [])
        self._trending_language: str = self.config.get("trending_language", "python")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AI-News-Aggregator-Bot/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._build_headers(),
                timeout=30.0,
            )
        return self._client

    @staticmethod
    def _check_rate_limit(response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) <= 1:
            reset_ts = response.headers.get("X-RateLimit-Reset", "0")
            reset_at = datetime.fromtimestamp(int(reset_ts), tz=timezone.utc)
            logger.warning(
                "GitHub rate limit nearly exhausted. Resets at %s", reset_at.isoformat()
            )

    # ------------------------------------------------------------------
    # Release fetching
    # ------------------------------------------------------------------

    async def _fetch_releases(self) -> list[RawArticle]:
        articles: list[RawArticle] = []
        client = await self._get_client()

        for repo in self._repos:
            url = f"https://api.github.com/repos/{repo}/releases?per_page=5"
            try:
                response = await client.get(url)
                self._check_rate_limit(response)
                if response.status_code != 200:
                    logger.warning(
                        "GitHub releases request failed for %s: %s", repo, response.status_code
                    )
                    continue

                releases = response.json()
                for rel in releases:
                    published = None
                    if rel.get("published_at"):
                        published = datetime.fromisoformat(
                            rel["published_at"].replace("Z", "+00:00")
                        )

                    articles.append(
                        RawArticle(
                            title=f"[Release] {repo} — {rel.get('name') or rel.get('tag_name', '')}",
                            url=rel.get("html_url", ""),
                            raw_content=rel.get("body", "") or "",
                            source_name=self.SOURCE_NAME,
                            published_at=published,
                            author=rel.get("author", {}).get("login"),
                            metadata={
                                "type": "release",
                                "repo": repo,
                                "tag": rel.get("tag_name"),
                                "prerelease": rel.get("prerelease", False),
                            },
                        )
                    )
            except httpx.HTTPError as exc:
                logger.error("HTTP error fetching releases for %s: %s", repo, exc)

        return articles

    # ------------------------------------------------------------------
    # Trending / search
    # ------------------------------------------------------------------

    async def _fetch_trending(self) -> list[RawArticle]:
        articles: list[RawArticle] = []
        client = await self._get_client()

        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        query = f"topic:ai language:{self._trending_language} created:>{since_date}"
        url = "https://api.github.com/search/repositories"
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": 20}

        try:
            response = await client.get(url, params=params)
            self._check_rate_limit(response)
            if response.status_code != 200:
                logger.warning("GitHub search request failed: %s", response.status_code)
                return articles

            data = response.json()
            for item in data.get("items", []):
                created = None
                if item.get("created_at"):
                    created = datetime.fromisoformat(
                        item["created_at"].replace("Z", "+00:00")
                    )

                articles.append(
                    RawArticle(
                        title=f"[Trending] {item.get('full_name', '')} — {item.get('description', '') or ''}",
                        url=item.get("html_url", ""),
                        raw_content=item.get("description", "") or "",
                        source_name=self.SOURCE_NAME,
                        published_at=created,
                        author=item.get("owner", {}).get("login"),
                        metadata={
                            "type": "trending",
                            "stars": item.get("stargazers_count", 0),
                            "language": item.get("language"),
                            "forks": item.get("forks_count", 0),
                            "topics": item.get("topics", []),
                        },
                    )
                )
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching trending repos: %s", exc)

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch GitHub releases and trending repos, returning normalised articles."""
        articles: list[RawArticle] = []
        try:
            release_articles = await self._fetch_releases()
            trending_articles = await self._fetch_trending()
            articles.extend(release_articles)
            articles.extend(trending_articles)
        except Exception:
            logger.exception("Unexpected error in GitHubScraper.scrape")
        return articles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
