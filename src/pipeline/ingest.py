"""IngestStage — converts a RawArticle into a persisted Article ORM instance."""

from __future__ import annotations

import hashlib
import logging
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.scrapers.article_fetcher import fetch_article, _is_google_news_url
from src.scrapers.base import RawArticle
from src.scrapers.content_cleaner import ContentCleaner

logger = logging.getLogger(__name__)

# Minimum body length we consider "useful" content. Below this we'll try to
# fetch the original page and extract a fuller body.
_MIN_USEFUL_CONTENT = 400


class IngestStage:
    """First pipeline stage: persist a RawArticle into the database.

    Responsibilities:
    - Generate a SHA-256 hash of the article URL for fast dedup lookups.
    - Run ContentCleaner on raw HTML/content to produce markdown.
    - When RSS content is sparse or the URL is a Google News redirect, fetch
      the actual article page and extract title, body, image, author, etc.
    - Create and flush (but not commit) the Article row so it has an ``id``
      for downstream stages.
    """

    def __init__(self, session: AsyncSession, source_id: int | None = None, **kwargs):
        self.session = session
        self.source_id = source_id

    async def process(self, raw_article: RawArticle) -> Article | None:
        """Ingest a raw article and return the persisted Article instance."""

        # Start from what the scraper supplied
        original_url = raw_article.url
        final_url = original_url
        title = (raw_article.title or "").strip()
        author = raw_article.author
        published_at = raw_article.published_at
        scraper_image = raw_article.metadata.get("image_url") or None
        image_url = scraper_image

        # Convert RSS-supplied HTML/text to markdown
        markdown_content = ContentCleaner.clean(raw_article.raw_content)

        needs_fetch = (
            _is_google_news_url(original_url)
            or len(markdown_content) < _MIN_USEFUL_CONTENT
            or not title
            or not image_url
        )

        fetch_payload: dict = {}
        if needs_fetch:
            logger.info(
                "Fetching full article page (rss_body_len=%d google_news=%s) url=%s",
                len(markdown_content),
                _is_google_news_url(original_url),
                original_url,
            )
            fetch_payload = await fetch_article(original_url)
            final_url = fetch_payload.get("resolved_url") or final_url

            body = fetch_payload.get("body_markdown") or ""
            if len(body) > len(markdown_content):
                markdown_content = body

            if not title and fetch_payload.get("title"):
                title = fetch_payload["title"].strip()
            if not author and fetch_payload.get("author"):
                author = fetch_payload["author"]
            if published_at is None and fetch_payload.get("published_at"):
                published_at = fetch_payload["published_at"]
            if not image_url and fetch_payload.get("image_url"):
                image_url = fetch_payload["image_url"]

        # Last-resort title fallbacks
        if not title:
            title = ContentCleaner.extract_title(raw_article.raw_content) or ""
        if not title.strip():
            title = "Untitled"

        # URL deduplication uses the resolved URL — this prevents storing the
        # same article twice when first seen via Google News and again direct.
        url_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()
        existing = await self.session.execute(
            select(Article.id).where(Article.url_hash == url_hash)
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug("Skipping already-ingested url_hash=%s", url_hash)
            return None

        if published_at is not None and published_at.tzinfo is not None:
            published_at = published_at.astimezone(timezone.utc).replace(tzinfo=None)

        logger.info(
            "Ingest result: original=%s resolved=%s title=%r body_len=%d image=%s",
            original_url,
            final_url,
            (title or "")[:60],
            len(markdown_content),
            image_url,
        )

        article = Article(
            source_id=self.source_id,
            title=title.strip()[:1024],
            url=final_url[:2048],
            url_hash=url_hash,
            raw_content=raw_article.raw_content,
            markdown_content=markdown_content,
            author=(author or None) and author[:512],
            published_at=published_at,
            pipeline_status="ingested",
            image_url=(image_url or None) and image_url[:2048],
        )

        self.session.add(article)
        await self.session.flush()  # assigns article.id

        logger.info(
            "Ingested article id=%s url_hash=%s title=%r",
            article.id,
            url_hash,
            article.title[:80],
        )
        return article
