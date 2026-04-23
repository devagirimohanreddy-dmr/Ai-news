"""LinkedIn scraper — fetches AI company updates from public LinkedIn pages."""

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

# CSS-like selectors implemented via regex for the public LinkedIn page HTML.
# LinkedIn public company pages expose some post content without authentication.
_POST_BLOCK_RE = re.compile(
    r'<div[^>]*class="[^"]*feed-shared-update[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_POST_TEXT_RE = re.compile(
    r'<span[^>]*class="[^"]*break-words[^"]*"[^>]*>(.*?)</span>',
    re.DOTALL,
)
_POST_TIME_RE = re.compile(
    r'<time[^>]*datetime="([^"]+)"',
)
_POST_IMAGE_RE = re.compile(
    r'<img[^>]+data-delayed-url="([^"]+)"',
)
_ARTICLE_LINK_RE = re.compile(
    r'<a[^>]+href="(https://www\.linkedin\.com/(?:pulse|posts)/[^"]+)"',
)
_COMPANY_NAME_RE = re.compile(
    r'<h1[^>]*>(.*?)</h1>',
    re.DOTALL,
)


def _clean_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class LinkedInScraper(BaseScraper):
    """Scrapes AI company updates from LinkedIn public pages.

    Config keys:
        company_urls: List of LinkedIn company page URLs to monitor.
    """

    SOURCE_NAME = "linkedin"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._company_urls: list[str] = self.config.get("company_urls", [])
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

    @staticmethod
    def _normalize_company_url(url: str) -> str:
        """Ensure URL points to the company's posts feed."""
        url = url.rstrip("/")
        if not url.endswith("/posts"):
            url += "/posts"
        return url

    async def _fetch_company(self, company_url: str) -> list[RawArticle]:
        """Fetch and parse posts from a single LinkedIn company page."""
        articles: list[RawArticle] = []
        client = await self._get_client()
        posts_url = self._normalize_company_url(company_url)

        try:
            response = await client.get(posts_url)

            if response.status_code != 200:
                logger.warning(
                    "LinkedIn request failed for %s: %s", company_url, response.status_code
                )
                return articles

            html = response.text

            # Try to extract company name from page
            company_match = _COMPANY_NAME_RE.search(html)
            company_name = _clean_html(company_match.group(1)) if company_match else company_url.split("/")[-1]

            # Extract article/post links
            article_links = _ARTICLE_LINK_RE.findall(html)

            # Extract post text blocks
            post_blocks = _POST_TEXT_RE.findall(html)
            post_times = _POST_TIME_RE.findall(html)
            post_images = _POST_IMAGE_RE.findall(html)

            # Build articles from extracted content
            num_posts = max(len(post_blocks), len(article_links))
            for i in range(num_posts):
                text = _clean_html(post_blocks[i]) if i < len(post_blocks) else ""
                if not text and i < len(article_links):
                    text = f"LinkedIn post: {article_links[i]}"

                if not text or len(text) < 20:
                    continue

                # Determine URL
                post_url = article_links[i] if i < len(article_links) else f"{posts_url}#post-{i}"

                # Parse time
                published_at: datetime | None = None
                if i < len(post_times):
                    try:
                        published_at = datetime.fromisoformat(
                            post_times[i].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                # Image
                image_url = post_images[i] if i < len(post_images) else None

                # Title from first ~80 chars of text
                title = text[:80].strip()
                if len(text) > 80:
                    title += "..."

                articles.append(
                    RawArticle(
                        title=f"[LinkedIn] {company_name}: {title}",
                        url=post_url,
                        raw_content=text,
                        source_name=self.SOURCE_NAME,
                        published_at=published_at,
                        author=company_name,
                        metadata={
                            "company_url": company_url,
                            "company_name": company_name,
                            "image_url": image_url,
                        },
                    )
                )

        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching LinkedIn %s: %s", company_url, exc)
        except Exception:
            logger.exception("Unexpected error parsing LinkedIn page %s", company_url)

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch posts from all configured LinkedIn company pages."""
        articles: list[RawArticle] = []
        try:
            for url in self._company_urls:
                company_articles = await self._fetch_company(url)
                articles.extend(company_articles)
        except Exception:
            logger.exception("Unexpected error in LinkedInScraper.scrape")

        logger.info("Fetched %d posts from %d LinkedIn companies", len(articles), len(self._company_urls))
        return articles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
