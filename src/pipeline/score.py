"""ScoreStage — computes article importance score from rules + source + LLM."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.scoring_rules import (
    BREAKING_KEYWORDS,
    MAX_KEYWORD_SCORE,
    MAX_LLM_SCORE,
    MAX_SOURCE_PRIORITY_SCORE,
    MAX_TOTAL_SCORE,
    SCORING_SYSTEM_PROMPT,
    SCORING_USER_PROMPT_TEMPLATE,
)
from src.config.settings import settings
from src.models.article import Article
from src.pipeline.base import PipelineStage

logger = logging.getLogger(__name__)


class ScoreStage(PipelineStage):
    """Fourth pipeline stage: compute importance score.

    The score is the sum of three independent components, capped at 10:

    1. **Rule-based keyword score (0-3)** — each hit against the
       ``BREAKING_KEYWORDS`` list adds +1, up to ``MAX_KEYWORD_SCORE``.
    2. **Source priority score (0-3)** — sourced from ``article.source.priority``
       (defaults to 1 when no source is attached).
    3. **LLM significance score (0-4)** — the LLM rates importance on a 1-10
       scale; this is normalised to 0-4.

    If the combined score meets or exceeds ``settings.BREAKING_NEWS_THRESHOLD``
    the article is flagged as breaking news.
    """

    def __init__(self, session: AsyncSession, llm_router=None, **kwargs):
        self.session = session
        self.llm_router = llm_router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, article: Article) -> Article | None:
        keyword_score = self._keyword_score(article)
        source_score = self._source_priority_score(article)
        llm_score = await self._llm_score(article)

        combined = min(keyword_score + source_score + llm_score, MAX_TOTAL_SCORE)

        article.importance_score = combined
        article.is_breaking = combined >= settings.BREAKING_NEWS_THRESHOLD
        article.pipeline_status = "scored"

        await self.session.flush()

        logger.info(
            "Article id=%s scored %d (kw=%d src=%d llm=%d) breaking=%s",
            article.id,
            combined,
            keyword_score,
            source_score,
            llm_score,
            article.is_breaking,
        )
        return article

    # ------------------------------------------------------------------
    # Component scores
    # ------------------------------------------------------------------

    @staticmethod
    def _keyword_score(article: Article) -> int:
        """Count keyword hits in title + content.  Each distinct hit = +1, max 3."""
        text = (
            f"{article.title} {article.markdown_content or ''}"
        ).lower()

        hits = 0
        for keyword in BREAKING_KEYWORDS:
            if keyword.lower() in text:
                hits += 1
            if hits >= MAX_KEYWORD_SCORE:
                break
        return hits

    @staticmethod
    def _source_priority_score(article: Article) -> int:
        """Return the source's priority value, clamped to [0, MAX_SOURCE_PRIORITY_SCORE]."""
        if article.source is not None:
            priority = article.source.priority or 1
        else:
            priority = 1
        return min(max(priority, 0), MAX_SOURCE_PRIORITY_SCORE)

    async def _llm_score(self, article: Article) -> int:
        """Ask the LLM for a 1-10 importance rating and normalise to 0-4."""
        if self.llm_router is None:
            return 0

        content_snippet = (article.markdown_content or "")[:1000]
        user_prompt = SCORING_USER_PROMPT_TEMPLATE.format(
            title=article.title,
            content=content_snippet,
        )

        try:
            response = await self.llm_router.generate(
                prompt=user_prompt,
                system_prompt=SCORING_SYSTEM_PROMPT,
                json_mode=True,
                task_type="score",
            )
            data = json.loads(response.text)
            raw_score = int(data.get("score", 5))

            # Clamp to 1-10 then normalise to 0-4
            raw_score = max(1, min(raw_score, 10))
            normalised = round((raw_score - 1) * MAX_LLM_SCORE / 9)
            return normalised

        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning(
                "Failed to parse LLM score for article id=%s",
                article.id,
                exc_info=True,
            )
            return 0
        except Exception:
            logger.warning(
                "LLM scoring failed for article id=%s",
                article.id,
                exc_info=True,
            )
            return 0
