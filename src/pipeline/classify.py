"""ClassifyStage — assigns 1-3 categories to an article via LLM + keyword fallback."""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.classification_prompts import (
    CLASSIFICATION_SYSTEM_PROMPT,
    CLASSIFICATION_USER_PROMPT_TEMPLATE,
    KEYWORD_CATEGORY_MAP,
)
from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.pipeline.base import PipelineStage

logger = logging.getLogger(__name__)


class ClassifyStage(PipelineStage):
    """Third pipeline stage: classify article into 1-3 categories.

    Primary path: ask the LLM to classify based on title + first 500 chars
    of markdown content.

    Fallback path: scan title + content for keywords that map to known
    categories.  This is used when the LLM router is unavailable or returns
    an unparseable response.
    """

    def __init__(self, session: AsyncSession, llm_router=None, **kwargs):
        self.session = session
        self.llm_router = llm_router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, article: Article) -> Article | None:
        category_names: list[str] = []

        # Try LLM classification first
        if self.llm_router is not None:
            category_names = await self._classify_via_llm(article)

        # Fallback to keyword matching
        if not category_names:
            category_names = self._classify_via_keywords(article)
            if category_names:
                logger.info(
                    "Article id=%s classified via keyword fallback: %s",
                    article.id,
                    category_names,
                )

        # Resolve category names -> DB rows and create join-table entries
        if category_names:
            await self._persist_categories(article, category_names)

        article.pipeline_status = "classified"
        await self.session.flush()

        logger.info(
            "Article id=%s classified into %d categories",
            article.id,
            len(category_names),
        )
        return article

    # ------------------------------------------------------------------
    # LLM classification
    # ------------------------------------------------------------------

    async def _classify_via_llm(self, article: Article) -> list[str]:
        """Call LLM and parse the JSON response into a list of category names."""
        content_snippet = (article.markdown_content or "")[:500]
        user_prompt = CLASSIFICATION_USER_PROMPT_TEMPLATE.format(
            title=article.title,
            content=content_snippet,
        )

        try:
            response = await self.llm_router.generate(
                prompt=user_prompt,
                system_prompt=CLASSIFICATION_SYSTEM_PROMPT,
                json_mode=True,
                task_type="classify",
            )
            data = json.loads(response.text)
            categories = data.get("categories", [])

            # Validate: must be a list of strings, max 3
            if (
                isinstance(categories, list)
                and all(isinstance(c, str) for c in categories)
            ):
                return categories[:3]

            logger.warning(
                "LLM returned unexpected classification format: %s",
                data,
            )
            return []

        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse LLM classification JSON for article id=%s",
                article.id,
                exc_info=True,
            )
            return []
        except Exception:
            logger.warning(
                "LLM classification failed for article id=%s; will use keyword fallback",
                article.id,
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Keyword fallback
    # ------------------------------------------------------------------

    def _classify_via_keywords(self, article: Article) -> list[str]:
        """Scan title + content for known keywords and return matching categories."""
        text = f"{article.title} {(article.markdown_content or '')[:1000]}".lower()

        matched_categories: set[str] = set()
        for keyword, cat_names in KEYWORD_CATEGORY_MAP.items():
            if keyword.lower() in text:
                matched_categories.update(cat_names)
            # Cap at 3 categories
            if len(matched_categories) >= 3:
                break

        return list(matched_categories)[:3]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_categories(
        self, article: Article, category_names: list[str]
    ) -> None:
        """Look up category IDs by name and create ArticleCategory rows."""
        result = await self.session.execute(
            select(Category).where(Category.name.in_(category_names))
        )
        categories = result.scalars().all()

        for cat in categories:
            # Avoid duplicate join-table rows if re-running classify
            exists = await self.session.execute(
                select(ArticleCategory).where(
                    ArticleCategory.article_id == article.id,
                    ArticleCategory.category_id == cat.id,
                )
            )
            if exists.scalar_one_or_none() is None:
                self.session.add(
                    ArticleCategory(article_id=article.id, category_id=cat.id)
                )

        await self.session.flush()
