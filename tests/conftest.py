"""Shared test fixtures for integration tests.

Provides:
- SQLite in-memory async engine (via aiosqlite) with all tables created
- Async session fixture that wraps each test in a transaction
- Seeded categories matching the 11 standard AI news categories
- Sample articles in various pipeline states
- Mock LLM router returning canned responses per task_type
- Mock scraper that returns predictable RawArticle data
- Session factory override so production code uses the test DB
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.models.base import Base
from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.models.source import Source
from src.models.subscription import Subscription
from src.models.summary import Summary
from src.models.post_log import PostLog
from src.pipeline.orchestrator import ArticlePipeline as _BaseArticlePipeline
from src.pipeline.ingest import IngestStage
from src.scrapers.base import RawArticle

# Re-export the UserPreference model so its table is created too
from src.bot.commands.settings import UserPreference  # noqa: F401


# ---------------------------------------------------------------------------
# Database engine & session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_engine():
    """Create an in-memory SQLite async engine with all tables.

    Uses ``pool_pre_ping`` and ``StaticPool`` so that every connection
    shares the same in-memory database (SQLite :memory: is per-connection).
    """
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # Map PostgreSQL JSONB to generic JSON so SQLite can handle it
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        pass  # placeholder for any SQLite pragmas

    # Patch JSONB columns to render as JSON for SQLite
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(async_engine):
    """Provide an async session for tests.

    Each test gets its own session. The session is rolled back after the
    test so tests don't pollute each other.
    """
    factory = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with factory() as session:
        yield session
        # Roll back any uncommitted changes after the test
        await session.rollback()


@pytest_asyncio.fixture()
async def session_factory(async_engine):
    """Return a session factory bound to the in-memory test engine."""
    return async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )


# ---------------------------------------------------------------------------
# Seed categories
# ---------------------------------------------------------------------------

SEED_CATEGORY_NAMES: list[str] = [
    "AI Models, Research & Benchmarks",
    "AI Engineering & Developer Tools",
    "Open Source AI Releases",
    "AI Products & Features",
    "AI Agents & Automation",
    "AI Use Cases & Applications",
    "AI Industry & Startups",
    "AI Infrastructure & Big Tech",
    "AI Policy, Safety & Governance",
    "AI Security & Risks",
    "Learning & Resources",
]


@pytest_asyncio.fixture()
async def seeded_categories(db_session: AsyncSession) -> list[Category]:
    """Insert the 11 standard categories and return them."""
    categories = []
    for name in SEED_CATEGORY_NAMES:
        cat = Category(name=name, enabled=True)
        db_session.add(cat)
        categories.append(cat)
    await db_session.flush()
    return categories


# ---------------------------------------------------------------------------
# Sample articles
# ---------------------------------------------------------------------------


def make_raw_article(**overrides) -> RawArticle:
    """Build a RawArticle with sensible defaults."""
    defaults = dict(
        title="GPT-5 Released: A Major Breakthrough in AI",
        url="https://example.com/gpt5-released",
        raw_content="<p>OpenAI has released GPT-5, a breakthrough model.</p>",
        source_name="TechNews",
        published_at=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        author="Jane Doe",
    )
    defaults.update(overrides)
    return RawArticle(**defaults)


@pytest_asyncio.fixture()
async def sample_articles(db_session: AsyncSession, seeded_categories) -> list[Article]:
    """Insert 3 sample articles in 'routed' status with summaries and categories."""
    now = datetime.now(timezone.utc)
    articles = []

    data = [
        {
            "title": "GPT-5 Released: A Major Breakthrough",
            "url": "https://example.com/gpt5",
            "importance_score": 9,
            "is_breaking": True,
            "category_index": 0,  # AI Models
        },
        {
            "title": "LangChain v0.3 Released with Agent Support",
            "url": "https://example.com/langchain-v03",
            "importance_score": 6,
            "is_breaking": False,
            "category_index": 1,  # AI Engineering
        },
        {
            "title": "EU Passes Comprehensive AI Regulation",
            "url": "https://example.com/eu-ai-act",
            "importance_score": 7,
            "is_breaking": False,
            "category_index": 8,  # AI Policy
        },
    ]

    for d in data:
        url_hash = hashlib.sha256(d["url"].encode()).hexdigest()
        article = Article(
            title=d["title"],
            url=d["url"],
            url_hash=url_hash,
            raw_content=f"<p>Content for {d['title']}</p>",
            markdown_content=f"Content for {d['title']}",
            author="Test Author",
            published_at=now - timedelta(hours=1),
            importance_score=d["importance_score"],
            is_breaking=d["is_breaking"],
            pipeline_status="routed",
        )
        db_session.add(article)
        await db_session.flush()

        # Add category link
        cat = seeded_categories[d["category_index"]]
        db_session.add(ArticleCategory(article_id=article.id, category_id=cat.id))

        # Add summary
        summary = Summary(
            article_id=article.id,
            headline=f"Headline: {d['title']}",
            summary_text=f"Summary of {d['title']}. This is a test summary.",
            llm_provider="openai",
        )
        db_session.add(summary)

        articles.append(article)

    await db_session.flush()
    await db_session.commit()
    return articles


# ---------------------------------------------------------------------------
# Mock LLM router
# ---------------------------------------------------------------------------


@dataclass
class FakeLLMResponse:
    """Stand-in for src.llm.base.LLMResponse."""

    text: str
    provider: str = "openai"
    model: str = "gpt-4o"
    tokens_used: int = 100
    latency_ms: float = 200.0
    cost_estimate: float = 0.01


def make_mock_llm_router(
    responses: dict[str, str] | None = None,
) -> AsyncMock:
    """Create a mock LLM router returning canned JSON for each task_type.

    Parameters
    ----------
    responses : dict, optional
        Mapping of task_type -> JSON string response text.
    """
    default_responses = {
        "classify": json.dumps(
            {"categories": ["AI Models, Research & Benchmarks"]}
        ),
        "score": json.dumps({"score": 8, "reason": "Major model release"}),
        "summarize": json.dumps(
            {
                "summary": "OpenAI released GPT-5, a major advancement in AI.",
                "headline": "GPT-5: A New Era in AI",
            }
        ),
    }
    if responses:
        default_responses.update(responses)

    router = AsyncMock()

    async def _generate(
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
        task_type: str = "general",
    ) -> FakeLLMResponse:
        text = default_responses.get(task_type, '{"result": "ok"}')
        return FakeLLMResponse(text=text)

    router.generate = AsyncMock(side_effect=_generate)
    return router


@pytest.fixture()
def mock_llm_router() -> AsyncMock:
    """Fixture returning a default mock LLM router."""
    return make_mock_llm_router()


# ---------------------------------------------------------------------------
# Mock scrapers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test-friendly ArticlePipeline
# ---------------------------------------------------------------------------


class ArticlePipeline(_BaseArticlePipeline):
    """ArticlePipeline subclass that explicitly refreshes lazy-loaded relationships.

    In async SQLAlchemy with SQLite (aiosqlite), lazy-loaded ``selectin``
    relationships on a freshly-created (not queried) ORM object trigger
    ``MissingGreenlet``.  This subclass refreshes the Article's relationships
    after the ClassifyStage and SummarizeStage so that the RouteStage can
    read ``article.categories`` and ``article.summaries`` without lazy-loading.
    """

    async def process(self, raw_article: RawArticle) -> Article | None:
        # --- Stage 1: Ingest ---
        try:
            article = await self._ingest.process(raw_article)
        except Exception:
            return None

        # --- Stages 2-6 ---
        for stage in self._stages:
            stage_name = stage.__class__.__name__
            try:
                result = await stage.process(article)
                if result is None:
                    return None
                article = result

                # After ClassifyStage or SummarizeStage, refresh relationships
                # so that downstream stages can access them without lazy loading.
                if stage_name in ("ClassifyStage", "SummarizeStage"):
                    await self.session.refresh(
                        article, ["categories", "summaries", "source"]
                    )
            except Exception:
                try:
                    await self.session.commit()
                except Exception:
                    pass
                return None

        # All stages passed — commit.
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            return None

        return article


# ---------------------------------------------------------------------------
# Mock scrapers
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_rss_scraper():
    """Return a mock RSS scraper that produces a fixed list of RawArticles."""
    scraper = AsyncMock()
    scraper.scrape = AsyncMock(
        return_value=[
            make_raw_article(
                title="RSS Article 1: AI Breakthrough",
                url="https://feeds.example.com/article-1",
                raw_content="<p>Breakthrough in AI research</p>",
            ),
        ]
    )
    scraper.close = AsyncMock()
    return scraper


@pytest.fixture()
def mock_github_scraper():
    """Return a mock GitHub scraper that produces release articles."""
    scraper = AsyncMock()
    scraper.scrape = AsyncMock(
        return_value=[
            make_raw_article(
                title="[Release] pytorch/pytorch - v2.5.0",
                url="https://github.com/pytorch/pytorch/releases/tag/v2.5.0",
                raw_content="PyTorch v2.5.0 release with major performance improvements",
                source_name="github",
            ),
        ]
    )
    scraper.close = AsyncMock()
    return scraper
