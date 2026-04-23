"""IngestStage — converts a RawArticle into a persisted Article ORM instance."""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.scrapers.base import RawArticle
from src.scrapers.content_cleaner import ContentCleaner

logger = logging.getLogger(__name__)


class IngestStage:
    """First pipeline stage: persist a RawArticle into the database.

    Responsibilities:
    - Generate a SHA-256 hash of the article URL for fast dedup lookups.
    - Run ContentCleaner on raw HTML/content to produce markdown.
    - Create and flush (but not commit) the Article row so it has an ``id``
      for downstream stages.
    """

    def __init__(self, session: AsyncSession, **kwargs):
        self.session = session

    async def process(self, raw_article: RawArticle) -> Article:
        """Ingest a raw article and return the persisted Article instance."""
        url_hash = hashlib.sha256(raw_article.url.encode("utf-8")).hexdigest()

        # Clean raw content -> markdown
        markdown_content = ContentCleaner.clean(raw_article.raw_content)

        # If the scraper already provided a title, use it; otherwise try to
        # extract one from the raw HTML.
        title = raw_article.title
        if not title or not title.strip():
            title = ContentCleaner.extract_title(raw_article.raw_content)
        if not title or not title.strip():
            title = "Untitled"

        article = Article(
            title=title.strip(),
            url=raw_article.url,
            url_hash=url_hash,
            raw_content=raw_article.raw_content,
            markdown_content=markdown_content,
            author=raw_article.author,
            published_at=raw_article.published_at,
            pipeline_status="ingested",
        )

        # Resolve source_id if the scraper provided a source name.  We leave
        # it as None if unresolved — the column is nullable.
        self.session.add(article)
        await self.session.flush()  # assigns article.id

        logger.info(
            "Ingested article id=%s url_hash=%s title=%r",
            article.id,
            url_hash,
            article.title[:80],
        )
        return article
