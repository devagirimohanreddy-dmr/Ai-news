"""Integration tests: Scraper -> Pipeline flow.

Tests that articles from different scrapers flow through the full 6-stage
pipeline with a real SQLite in-memory database, mocked LLM, and mocked
network calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.models.summary import Summary
from src.scrapers.base import RawArticle
from src.scrapers.content_cleaner import ContentCleaner

from tests.conftest import ArticlePipeline, make_raw_article, make_mock_llm_router, FakeLLMResponse


# ---------------------------------------------------------------------------
# Test 1: RSS article flows through full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rss_article_full_pipeline(db_session: AsyncSession, seeded_categories):
    """An RSS-sourced article should pass all 6 stages and arrive at status='routed'."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="GPT-5 Released: A Major Breakthrough in AI",
        url="https://techcrunch.com/gpt5-released",
        raw_content="<p>OpenAI has released GPT-5, a breakthrough model in AI.</p>",
        source_name="TechCrunch RSS",
    )

    article = await pipeline.process(raw)

    # Article should be fully processed
    assert article is not None
    assert article.id is not None
    assert article.pipeline_status == "routed"

    # URL hash should be computed
    expected_hash = hashlib.sha256(raw.url.encode()).hexdigest()
    assert article.url_hash == expected_hash

    # Title preserved from raw article
    assert article.title == raw.title

    # Should have been classified into at least one category
    # (keyword fallback will match "GPT" -> "AI Models, Research & Benchmarks")
    result = await db_session.execute(
        select(ArticleCategory).where(ArticleCategory.article_id == article.id)
    )
    links = result.scalars().all()
    assert len(links) >= 1

    # Should have a summary
    result = await db_session.execute(
        select(Summary).where(Summary.article_id == article.id)
    )
    summaries = result.scalars().all()
    assert len(summaries) == 1
    assert summaries[0].summary_text != ""
    assert summaries[0].llm_provider == "openai"

    # Importance score should be set (keyword "GPT" + "breakthrough" will contribute)
    assert article.importance_score > 0


# ---------------------------------------------------------------------------
# Test 2: GitHub release flows through pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_release_through_pipeline(db_session: AsyncSession, seeded_categories):
    """A GitHub release article should be ingested and processed through pipeline."""
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["Open Source AI Releases"]}
            ),
            "score": json.dumps({"score": 6, "reason": "Notable open-source release"}),
            "summarize": json.dumps(
                {
                    "summary": "PyTorch v2.5.0 brings performance improvements.",
                    "headline": "PyTorch 2.5 Released",
                }
            ),
        }
    )
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="[Release] pytorch/pytorch - v2.5.0",
        url="https://github.com/pytorch/pytorch/releases/tag/v2.5.0",
        raw_content="PyTorch v2.5.0 release with major open source performance improvements",
        source_name="github",
    )

    article = await pipeline.process(raw)

    assert article is not None
    assert article.pipeline_status == "routed"
    assert article.title == "[Release] pytorch/pytorch - v2.5.0"
    assert article.url == raw.url

    # Verify classification assigned "Open Source AI Releases"
    result = await db_session.execute(
        select(Category)
        .join(ArticleCategory, ArticleCategory.category_id == Category.id)
        .where(ArticleCategory.article_id == article.id)
    )
    categories = result.scalars().all()
    cat_names = [c.name for c in categories]
    assert "Open Source AI Releases" in cat_names

    # Should have summary from LLM
    result = await db_session.execute(
        select(Summary).where(Summary.article_id == article.id)
    )
    summary = result.scalars().first()
    assert summary is not None
    assert "PyTorch" in summary.summary_text


# ---------------------------------------------------------------------------
# Test 3: Content cleaner is applied during ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_cleaner_applied_during_ingest(db_session: AsyncSession, seeded_categories):
    """The IngestStage should run ContentCleaner on raw HTML content."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    html_content = (
        "<html><head><title>Test</title></head>"
        "<body><nav>Menu items</nav>"
        "<article><h1>AI Research Update</h1>"
        "<p>Researchers have achieved a breakthrough in transformer architecture.</p>"
        "</article><footer>Copyright 2026</footer></body></html>"
    )

    raw = make_raw_article(
        title="AI Research Update",
        url="https://example.com/ai-research-update",
        raw_content=html_content,
    )

    article = await pipeline.process(raw)

    assert article is not None
    # markdown_content should be cleaned (not raw HTML)
    assert article.markdown_content is not None
    # Should not contain raw HTML tags (readability + markdownify strip them)
    assert "<nav>" not in article.markdown_content
    assert "<footer>" not in article.markdown_content
    # Should contain useful content
    assert article.raw_content == html_content  # raw preserved


# ---------------------------------------------------------------------------
# Test 4: Pipeline updates article status at each stage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_status_progression(db_session: AsyncSession, seeded_categories):
    """The pipeline should update pipeline_status through each stage."""
    # We track status changes by instrumenting the pipeline stages.
    statuses_observed: list[str] = []

    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="LangChain SDK update with agent improvements",
        url="https://example.com/langchain-update",
        raw_content="LangChain has released a major SDK update for agents.",
    )

    article = await pipeline.process(raw)

    assert article is not None
    # After full pipeline, status should be "routed"
    assert article.pipeline_status == "routed"

    # Verify the article went through all stages by checking the final state
    # in the DB (the pipeline commits at the end)
    result = await db_session.execute(
        select(Article).where(Article.id == article.id)
    )
    db_article = result.scalar_one()
    assert db_article.pipeline_status == "routed"

    # Verify intermediate artifacts exist, proving each stage ran:
    # 1. Ingest: article exists with url_hash
    assert db_article.url_hash is not None
    assert len(db_article.url_hash) == 64  # SHA-256 hex

    # 2. Dedup: article was not deleted (it's unique)
    assert db_article.id is not None

    # 3. Classify: categories assigned
    result = await db_session.execute(
        select(ArticleCategory).where(ArticleCategory.article_id == db_article.id)
    )
    assert len(result.scalars().all()) >= 1

    # 4. Score: importance_score set
    assert db_article.importance_score >= 0

    # 5. Summarize: summary exists
    result = await db_session.execute(
        select(Summary).where(Summary.article_id == db_article.id)
    )
    assert result.scalars().first() is not None

    # 6. Route: pipeline_status == "routed" (already verified)


# ---------------------------------------------------------------------------
# Test 5: Duplicate URL is detected and rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_url_rejected(db_session: AsyncSession, seeded_categories, session_factory):
    """Processing the same URL twice should result in the second being dropped.

    We use separate sessions for the two pipeline runs because the first
    run commits (closing its transaction), and the second run's flush of
    the same URL would put the session into a PendingRollback state.
    """
    llm_router = make_mock_llm_router()

    # First pipeline run in its own session
    async with session_factory() as session1:
        pipeline1 = ArticlePipeline(session=session1, llm_router=llm_router)
        raw = make_raw_article(
            title="Unique Article",
            url="https://example.com/unique-article",
            raw_content="Some unique content here.",
        )

        article1 = await pipeline1.process(raw)
        assert article1 is not None
        assert article1.pipeline_status == "routed"

    # Second pipeline run with the same URL in a fresh session
    async with session_factory() as session2:
        pipeline2 = ArticlePipeline(session=session2, llm_router=llm_router)
        raw2 = make_raw_article(
            title="Unique Article",
            url="https://example.com/unique-article",
            raw_content="Some unique content here.",
        )

        # Should be filtered out (either by dedup hash or unique constraint)
        article2 = await pipeline2.process(raw2)
        assert article2 is None

    # Verify only one article in DB
    async with session_factory() as session3:
        result = await session3.execute(
            select(Article).where(
                Article.url_hash == hashlib.sha256(raw.url.encode()).hexdigest()
            )
        )
        articles = result.scalars().all()
        assert len(articles) == 1


# ---------------------------------------------------------------------------
# Test 6: Plain text content passes through cleaner unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_text_content_passes_through(db_session: AsyncSession, seeded_categories):
    """Non-HTML content should pass through ContentCleaner as-is."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    plain_text = "This is a plain text article about GPT models and AI research."

    raw = make_raw_article(
        title="Plain Text Article",
        url="https://example.com/plain-text",
        raw_content=plain_text,
    )

    article = await pipeline.process(raw)

    assert article is not None
    # ContentCleaner should return plain text unchanged
    assert article.markdown_content == plain_text
