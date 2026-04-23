"""Integration tests: /summarize end-to-end flow.

Tests the full summarize lifecycle: URL submission -> scraping -> pipeline ->
summary card returned -> article stored in DB.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.models.summary import Summary
from src.bot.commands.summarize import handle_summarize
from src.scrapers.base import RawArticle

from tests.conftest import ArticlePipeline, make_raw_article, make_mock_llm_router, FakeLLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_factory(llm_router):
    """Create a pipeline factory function suitable for handle_summarize."""

    def factory(session):
        return ArticlePipeline(session=session, llm_router=llm_router)

    return factory


# ---------------------------------------------------------------------------
# Test 1: /summarize full flow — scrape, process, return card, persist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_full_flow(db_session: AsyncSession, seeded_categories):
    """Submitting a new URL should process it through the pipeline and return a summary card."""
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["AI Models, Research & Benchmarks"]}
            ),
            "score": json.dumps({"score": 7, "reason": "Significant research"}),
            "summarize": json.dumps(
                {
                    "summary": "Researchers published a new method for model training.",
                    "headline": "New Training Method",
                }
            ),
        }
    )
    pipeline_factory = _make_pipeline_factory(llm_router)

    url = "https://example.com/article/new-training-method"

    # Patch Celery to avoid broker requirements
    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()

        result = await handle_summarize(
            db_session, url, pipeline_factory
        )

    # Should return a card
    assert "card" in result
    assert result["card"] is not None
    assert result["card"]["type"] == "AdaptiveCard"

    # Should have a text response
    assert "text" in result
    assert "summary" in result["text"].lower()

    # Verify article was stored in DB
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    stmt = select(Article).where(Article.url_hash == url_hash)
    db_result = await db_session.execute(stmt)
    article = db_result.scalar_one_or_none()

    assert article is not None
    assert article.url == url
    assert article.pipeline_status == "routed"

    # Verify all fields populated
    assert article.url_hash == url_hash
    assert article.title is not None and article.title != ""

    # Verify summary was created
    stmt = select(Summary).where(Summary.article_id == article.id)
    db_result = await db_session.execute(stmt)
    summary = db_result.scalar_one_or_none()
    assert summary is not None
    assert "training" in summary.summary_text.lower() or len(summary.summary_text) > 0
    assert summary.llm_provider == "openai"

    # Verify category assignment
    stmt = select(ArticleCategory).where(ArticleCategory.article_id == article.id)
    db_result = await db_session.execute(stmt)
    cat_links = db_result.scalars().all()
    assert len(cat_links) >= 1

    # Verify importance score was set
    assert article.importance_score > 0


# ---------------------------------------------------------------------------
# Test 2: Duplicate URL returns cached summary without re-scraping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_url_returns_cached_summary(
    db_session: AsyncSession, sample_articles
):
    """Summarizing a URL already in the DB should return the cached summary."""
    # sample_articles[0] has url="https://example.com/gpt5"
    existing = sample_articles[0]
    url = existing.url

    result = await handle_summarize(db_session, url, pipeline_factory=None)

    # Should return cached summary
    assert "card" in result
    assert result["card"] is not None
    assert "text" in result
    assert "cached" in result["text"].lower()

    # The card should contain the existing article's data
    card_body = result["card"]["body"]
    title_block = card_body[0]
    assert existing.title in title_block.get("text", "")


@pytest.mark.asyncio
async def test_duplicate_url_does_not_call_pipeline(
    db_session: AsyncSession, sample_articles
):
    """When a URL already has a summary, the pipeline should NOT be invoked."""
    existing = sample_articles[0]
    url = existing.url

    pipeline_factory = MagicMock()  # Should never be called

    result = await handle_summarize(db_session, url, pipeline_factory)

    # Pipeline factory should not have been called
    pipeline_factory.assert_not_called()

    # Should still return a valid card
    assert result["card"] is not None


# ---------------------------------------------------------------------------
# Test 3: Invalid URL returns error message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_url_returns_error(db_session: AsyncSession):
    """An invalid URL should return an error without touching the pipeline."""
    result = await handle_summarize(db_session, "not-a-valid-url", None)

    assert "text" in result
    assert "valid URL" in result["text"] or "does not look like" in result["text"]
    assert result.get("card") is None


@pytest.mark.asyncio
async def test_empty_url_returns_usage(db_session: AsyncSession):
    """No URL should return usage instructions."""
    result = await handle_summarize(db_session, "", None)

    assert "text" in result
    assert "provide" in result["text"].lower() or "usage" in result["text"].lower()
    assert result.get("card") is None


@pytest.mark.asyncio
async def test_url_with_spaces_returns_error(db_session: AsyncSession):
    """A URL with spaces should be rejected."""
    result = await handle_summarize(db_session, "https://example .com/article", None)

    assert "text" in result
    assert result.get("card") is None


# ---------------------------------------------------------------------------
# Test 4: Pipeline not available returns helpful message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_not_available(db_session: AsyncSession):
    """When pipeline_factory is None and URL is not cached, return an error."""
    url = "https://example.com/brand-new-article"

    result = await handle_summarize(db_session, url, pipeline_factory=None)

    assert "text" in result
    assert "not available" in result["text"].lower() or "try again" in result["text"].lower()
    assert result.get("card") is None


# ---------------------------------------------------------------------------
# Test 5: Pipeline processes all 6 stages for user-submitted URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_processes_all_pipeline_stages(
    db_session: AsyncSession, seeded_categories
):
    """The /summarize pipeline should go through ingest -> dedup -> classify -> score -> summarize -> route."""
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["AI Products & Features"]}
            ),
            "score": json.dumps({"score": 5, "reason": "Product update"}),
            "summarize": json.dumps(
                {
                    "summary": "ChatGPT gets a major UI update.",
                    "headline": "ChatGPT UI Refresh",
                }
            ),
        }
    )
    pipeline_factory = _make_pipeline_factory(llm_router)

    url = "https://example.com/chatgpt-ui-update"

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()

        result = await handle_summarize(db_session, url, pipeline_factory)

    assert result["card"] is not None

    # Verify the article went through all stages by checking DB state
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    stmt = select(Article).where(Article.url_hash == url_hash)
    db_result = await db_session.execute(stmt)
    article = db_result.scalar_one()

    # Final status confirms all stages completed
    assert article.pipeline_status == "routed"

    # Classify stage: category assigned
    stmt = select(ArticleCategory).where(ArticleCategory.article_id == article.id)
    db_result = await db_session.execute(stmt)
    assert len(db_result.scalars().all()) >= 1

    # Score stage: importance_score set
    assert article.importance_score >= 0

    # Summarize stage: summary exists
    stmt = select(Summary).where(Summary.article_id == article.id)
    db_result = await db_session.execute(stmt)
    summary = db_result.scalar_one()
    assert summary.headline == "ChatGPT UI Refresh"
    assert "ChatGPT" in summary.summary_text

    # Route stage: PostLog created
    from src.models.post_log import PostLog

    stmt = select(PostLog).where(PostLog.article_id == article.id)
    db_result = await db_session.execute(stmt)
    post_log = db_result.scalar_one()
    assert post_log.post_type in ("alert", "digest")
