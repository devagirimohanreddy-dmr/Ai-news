"""DedupStage — filters out articles that already exist in the database."""

from __future__ import annotations

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.pipeline.base import PipelineStage

logger = logging.getLogger(__name__)


class DedupStage(PipelineStage):
    """Second pipeline stage: duplicate detection.

    Two checks are performed:

    1. **Exact URL match** — compare the SHA-256 hash of the URL against
       existing rows.  This is an O(1) index lookup.
    2. **Fuzzy title match** — use PostgreSQL ``pg_trgm`` extension's
       ``similarity()`` function to find titles with > 0.8 similarity.
       This catches republished / syndicated content that appears at
       different URLs.

    If either check finds an existing article, the current article is
    considered a duplicate and ``None`` is returned to halt the pipeline.
    """

    def __init__(self, session: AsyncSession, **kwargs):
        self.session = session

    async def process(self, article: Article) -> Article | None:
        # --- 1. Exact URL hash match -----------------------------------------
        existing_by_hash = await self.session.execute(
            select(Article.id).where(
                Article.url_hash == article.url_hash,
                Article.id != article.id,
            )
        )
        if existing_by_hash.scalar_one_or_none() is not None:
            logger.info(
                "Duplicate detected (url_hash) for article id=%s url=%s",
                article.id,
                article.url,
            )
            # Remove the freshly-ingested duplicate from the session
            await self.session.delete(article)
            await self.session.flush()
            return None

        # --- 2. Fuzzy title match via pg_trgm --------------------------------
        try:
            similar = await self.session.execute(
                text(
                    "SELECT id FROM articles "
                    "WHERE id != :article_id "
                    "AND similarity(title, :title) > 0.8 "
                    "LIMIT 1"
                ),
                {"article_id": article.id, "title": article.title},
            )
            if similar.scalar_one_or_none() is not None:
                logger.info(
                    "Duplicate detected (title similarity) for article id=%s title=%r",
                    article.id,
                    article.title[:80],
                )
                await self.session.delete(article)
                await self.session.flush()
                return None
        except Exception:
            # pg_trgm may not be installed in all environments (tests, dev).
            # Fall back gracefully — URL-hash check is still effective.
            logger.debug(
                "pg_trgm similarity check unavailable; skipping fuzzy dedup",
                exc_info=True,
            )

        # --- Not a duplicate — advance status --------------------------------
        article.pipeline_status = "deduped"
        await self.session.flush()

        logger.info(
            "Article id=%s passed dedup checks", article.id,
        )
        return article
