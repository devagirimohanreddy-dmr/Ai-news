"""ArticlePipeline — orchestrates the 6-stage article processing pipeline."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.pipeline.ingest import IngestStage
from src.pipeline.dedup import DedupStage
from src.pipeline.classify import ClassifyStage
from src.pipeline.score import ScoreStage
from src.pipeline.summarize import SummarizeStage
from src.pipeline.route import RouteStage
from src.scrapers.base import RawArticle

logger = logging.getLogger(__name__)

# Ordered list of stage classes after IngestStage (which is handled
# separately because it takes a RawArticle instead of an Article).
_POST_INGEST_STAGES = [
    DedupStage,
    ClassifyStage,
    ScoreStage,
    SummarizeStage,
    RouteStage,
]


class ArticlePipeline:
    """Run a RawArticle through all 6 processing stages.

    Stages:
        1. **Ingest** — persist raw article, clean content, generate url_hash
        2. **Dedup** — check for duplicates (URL hash + title similarity)
        3. **Classify** — assign categories via LLM / keyword fallback
        4. **Score** — compute importance score
        5. **Summarize** — generate headline + summary via LLM
        6. **Route** — dispatch alerts, queue digest, notify subscribers

    Each stage receives an Article and returns it to the next stage.  If any
    stage returns ``None``, the pipeline halts for that article (e.g. dup
    detected).  An unhandled exception in a stage is caught, logged, and
    treated as a pipeline stop — the article retains whatever status it
    reached.
    """

    def __init__(self, session: AsyncSession, llm_router: Any = None):
        self.session = session
        self.llm_router = llm_router

        # Pre-instantiate stages so they share the session and router.
        self._ingest = IngestStage(session=session)
        self._stages = [
            cls(session=session, llm_router=llm_router)
            for cls in _POST_INGEST_STAGES
        ]

    # ------------------------------------------------------------------
    # Single-article processing
    # ------------------------------------------------------------------

    async def process(self, raw_article: RawArticle) -> Article | None:
        """Run *raw_article* through all pipeline stages.

        Returns the fully-processed Article, or ``None`` if it was
        filtered out (duplicate) or an error occurred.
        """
        # --- Stage 1: Ingest ---
        try:
            article = await self._ingest.process(raw_article)
        except Exception:
            logger.error(
                "Pipeline failed at IngestStage for url=%s",
                raw_article.url,
                exc_info=True,
            )
            return None

        # --- Stages 2-6 ---
        for stage in self._stages:
            stage_name = stage.__class__.__name__
            try:
                result = await stage.process(article)
                if result is None:
                    logger.info(
                        "Pipeline halted at %s for article id=%s",
                        stage_name,
                        article.id,
                    )
                    return None
                article = result
            except Exception:
                logger.error(
                    "Pipeline failed at %s for article id=%s",
                    stage_name,
                    article.id,
                    exc_info=True,
                )
                # Commit what we have so far so the article is not lost
                try:
                    await self.session.commit()
                except Exception:
                    logger.error(
                        "Failed to commit partial progress for article id=%s",
                        article.id,
                        exc_info=True,
                    )
                return None

        # All stages passed — commit the full transaction.
        try:
            await self.session.commit()
        except Exception:
            logger.error(
                "Failed to commit completed article id=%s",
                article.id,
                exc_info=True,
            )
            await self.session.rollback()
            return None

        logger.info(
            "Pipeline completed for article id=%s title=%r score=%d breaking=%s",
            article.id,
            article.title[:60],
            article.importance_score,
            article.is_breaking,
        )
        return article

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    async def process_batch(self, articles: list[RawArticle]) -> list[Article]:
        """Process multiple raw articles.  One failure does not block others.

        Returns a list of successfully processed articles (duplicates and
        failures are excluded).
        """
        results: list[Article] = []

        for raw in articles:
            try:
                article = await self.process(raw)
                if article is not None:
                    results.append(article)
            except Exception:
                logger.error(
                    "Unexpected error processing article url=%s",
                    raw.url,
                    exc_info=True,
                )

        logger.info(
            "Batch complete: %d/%d articles processed successfully",
            len(results),
            len(articles),
        )
        return results
