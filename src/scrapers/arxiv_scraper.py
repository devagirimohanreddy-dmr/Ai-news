"""arXiv scraper — fetches recent papers from configurable CS/AI categories."""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

ARXIV_API_BASE = "http://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

DEFAULT_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]


class ArxivScraper(BaseScraper):
    """Scrape recent papers from arXiv.

    Config keys:
        categories: List of arXiv category codes (default: cs.AI, cs.LG, cs.CL).
        max_results: Maximum papers per category query (default 50).
    """

    SOURCE_NAME = "arxiv"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._categories: list[str] = self.config.get("categories", DEFAULT_CATEGORIES)
        self._max_results: int = self.config.get("max_results", 50)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": "AI-News-Aggregator-Bot/0.1"},
                timeout=60.0,
            )
        return self._client

    @staticmethod
    def _parse_entries(xml_text: str) -> list[dict[str, Any]]:
        """Parse Atom XML feed from arXiv into a list of entry dicts."""
        entries: list[dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse arXiv XML: %s", exc)
            return entries

        for entry in root.findall(f"{ATOM_NS}entry"):
            title_el = entry.find(f"{ATOM_NS}title")
            summary_el = entry.find(f"{ATOM_NS}summary")
            published_el = entry.find(f"{ATOM_NS}published")

            # Collect authors
            authors = []
            for author_el in entry.findall(f"{ATOM_NS}author"):
                name_el = author_el.find(f"{ATOM_NS}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            # Collect links
            abs_link = ""
            pdf_link = ""
            for link_el in entry.findall(f"{ATOM_NS}link"):
                href = link_el.get("href", "")
                link_type = link_el.get("type", "")
                link_title = link_el.get("title", "")
                if link_title == "pdf" or link_type == "application/pdf":
                    pdf_link = href
                elif link_el.get("rel") == "alternate":
                    abs_link = href

            # Collect categories
            categories = []
            for cat_el in entry.findall(f"{ATOM_NS}category"):
                term = cat_el.get("term", "")
                if term:
                    categories.append(term)

            # Also check arxiv-namespaced primary category
            primary_cat_el = entry.find(f"{ARXIV_NS}primary_category")
            primary_category = ""
            if primary_cat_el is not None:
                primary_category = primary_cat_el.get("term", "")

            published = None
            if published_el is not None and published_el.text:
                try:
                    published = datetime.fromisoformat(
                        published_el.text.strip().replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            entries.append(
                {
                    "title": (title_el.text.strip() if title_el is not None and title_el.text else ""),
                    "summary": (summary_el.text.strip() if summary_el is not None and summary_el.text else ""),
                    "authors": authors,
                    "published": published,
                    "abs_link": abs_link,
                    "pdf_link": pdf_link,
                    "categories": categories,
                    "primary_category": primary_category,
                }
            )

        return entries

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    async def _fetch_category(self, category: str) -> list[RawArticle]:
        articles: list[RawArticle] = []
        client = await self._get_client()

        params = {
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self._max_results,
        }

        try:
            response = await client.get(ARXIV_API_BASE, params=params)
            if response.status_code != 200:
                logger.warning(
                    "arXiv query failed for %s: %s", category, response.status_code
                )
                return articles

            entries = self._parse_entries(response.text)
            for entry in entries:
                articles.append(
                    RawArticle(
                        title=entry["title"],
                        url=entry["abs_link"],
                        raw_content=entry["summary"],
                        source_name=self.SOURCE_NAME,
                        published_at=entry["published"],
                        author=", ".join(entry["authors"]) if entry["authors"] else None,
                        metadata={
                            "categories": entry["categories"],
                            "primary_category": entry["primary_category"],
                            "pdf_url": entry["pdf_link"],
                        },
                    )
                )
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching arXiv category %s: %s", category, exc)

        return articles

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Fetch recent papers from configured arXiv categories."""
        articles: list[RawArticle] = []
        try:
            for category in self._categories:
                cat_articles = await self._fetch_category(category)
                articles.extend(cat_articles)
        except Exception:
            logger.exception("Unexpected error in ArxivScraper.scrape")
        return articles

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
