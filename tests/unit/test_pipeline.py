"""Tests for the 6-stage article pipeline.

All database access and LLM calls are mocked so these run as fast unit tests
without external dependencies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import RawArticle

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _raw_article(**overrides) -> RawArticle:
    """Build a RawArticle with sensible defaults, allowing overrides."""
    defaults = dict(
        title="GPT-5 Released: A Major Breakthrough in AI",
        url="https://example.com/gpt5-released",
        raw_content="<p>OpenAI has released GPT-5, a breakthrough model.</p>",
        source_name="TechNews",
        published_at=datetime(2026, 4, 20, 12, 0),
        author="Jane Doe",
    )
    defaults.update(overrides)
    return RawArticle(**defaults)


@dataclass
class FakeLLMResponse:
    """Stand-in for src.llm.base.LLMResponse."""

    text: str
    provider: str = "openai"
    model: str = "gpt-4o"
    tokens_used: int = 100
    latency_ms: float = 200.0
    cost_estimate: float = 0.01


def _make_llm_router(responses: dict[str, str] | None = None) -> AsyncMock:
    """Create a mock LLM router that returns canned JSON for each task_type."""
    default_responses = {
        "classify": json.dumps(
            {"categories": ["AI Models, Research & Benchmarks"]}
        ),
        "score": json.dumps({"score": 7, "reason": "Major model release"}),
        "summarize": json.dumps(
            {
                "summary": "OpenAI released GPT-5, a major advancement.",
                "headline": "GPT-5 Launches",
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
        task_type: str = "",
    ):
        text = default_responses.get(task_type, "{}")
        return FakeLLMResponse(text=text)

    router.generate = AsyncMock(side_effect=_generate)
    return router


def _make_session() -> AsyncMock:
    """Create an AsyncMock that behaves like an AsyncSession.

    Key behaviours:
    - ``add()`` records added objects
    - ``flush()`` assigns an ``id`` to objects missing one
    - ``execute()`` returns an empty result by default
    - ``commit()`` / ``rollback()`` are no-ops
    """
    session = AsyncMock()
    session._added: list[Any] = []
    _id_counter = 1

    def _add(obj):
        session._added.append(obj)

    async def _flush():
        nonlocal _id_counter
        for obj in session._added:
            if hasattr(obj, "id") and obj.id is None:
                obj.id = _id_counter
                _id_counter += 1
        # Don't clear — some tests inspect added objects later

    async def _delete(obj):
        pass

    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock(side_effect=_flush)
    session.delete = AsyncMock(side_effect=_delete)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    # Default execute returns empty result (no duplicates, no categories)
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)

    return session


# ===========================================================================
# IngestStage tests
# ===========================================================================


class TestIngestStage:
    @pytest.mark.asyncio
    async def test_creates_article_with_url_hash(self):
        from src.pipeline.ingest import IngestStage

        session = _make_session()
        stage = IngestStage(session=session)
        raw = _raw_article()

        article = await stage.process(raw)

        expected_hash = hashlib.sha256(raw.url.encode("utf-8")).hexdigest()
        assert article is not None
        assert article.url_hash == expected_hash
        assert article.pipeline_status == "ingested"
        assert article.title == raw.title
        assert article.url == raw.url
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sets_markdown_content(self):
        from src.pipeline.ingest import IngestStage

        session = _make_session()
        stage = IngestStage(session=session)
        raw = _raw_article(
            raw_content="<html><body><p>Hello markdown world.</p></body></html>"
        )

        article = await stage.process(raw)

        # ContentCleaner should have produced something non-empty
        assert article.markdown_content is not None

    @pytest.mark.asyncio
    async def test_fallback_title_when_empty(self):
        from src.pipeline.ingest import IngestStage

        session = _make_session()
        stage = IngestStage(session=session)
        raw = _raw_article(title="", raw_content="plain text no html tags")

        article = await stage.process(raw)

        # With no HTML tags and no title, should fall back to "Untitled"
        assert article.title == "Untitled"

    @pytest.mark.asyncio
    async def test_preserves_author_and_published_at(self):
        from src.pipeline.ingest import IngestStage

        session = _make_session()
        stage = IngestStage(session=session)
        raw = _raw_article()

        article = await stage.process(raw)

        assert article.author == "Jane Doe"
        assert article.published_at == datetime(2026, 4, 20, 12, 0)


# ===========================================================================
# DedupStage tests
# ===========================================================================


class TestDedupStage:
    @pytest.mark.asyncio
    async def test_passes_unique_article(self):
        from src.pipeline.dedup import DedupStage

        session = _make_session()
        stage = DedupStage(session=session)

        article = MagicMock()
        article.id = 1
        article.url = "https://example.com/unique"
        article.url_hash = "abc123"
        article.title = "Unique Article"

        result = await stage.process(article)

        assert result is article
        assert article.pipeline_status == "deduped"

    @pytest.mark.asyncio
    async def test_rejects_duplicate_url_hash(self):
        from src.pipeline.dedup import DedupStage

        session = _make_session()

        # First execute() call returns a match (existing article id)
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = 42  # existing article id
        session.execute = AsyncMock(return_value=dup_result)

        stage = DedupStage(session=session)

        article = MagicMock()
        article.id = 2
        article.url = "https://example.com/duplicate"
        article.url_hash = "same_hash"
        article.title = "Duplicate Article"

        result = await stage.process(article)

        assert result is None
        session.delete.assert_awaited_once_with(article)

    @pytest.mark.asyncio
    async def test_rejects_similar_title(self):
        from src.pipeline.dedup import DedupStage

        session = _make_session()

        # First call (url hash) returns None, second call (title sim) returns match
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None

        title_match = MagicMock()
        title_match.scalar_one_or_none.return_value = 99

        session.execute = AsyncMock(side_effect=[no_match, title_match])

        stage = DedupStage(session=session)

        article = MagicMock()
        article.id = 3
        article.url = "https://example.com/similar-title"
        article.url_hash = "unique_hash"
        article.title = "Nearly Identical Article Title"

        result = await stage.process(article)

        assert result is None


# ===========================================================================
# ClassifyStage tests
# ===========================================================================


class TestClassifyStage:
    @pytest.mark.asyncio
    async def test_classifies_via_llm(self):
        from src.pipeline.classify import ClassifyStage

        session = _make_session()

        # Mock execute to return a Category object when looking up by name
        cat_obj = MagicMock()
        cat_obj.id = 1
        cat_obj.name = "AI Models, Research & Benchmarks"

        cat_result = MagicMock()
        cat_result.scalars.return_value.all.return_value = [cat_obj]

        # First call: category lookup; second call: ArticleCategory existence check
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(side_effect=[cat_result, no_match])

        llm_router = _make_llm_router()
        stage = ClassifyStage(session=session, llm_router=llm_router)

        article = MagicMock()
        article.id = 1
        article.title = "GPT-5 Released"
        article.markdown_content = "OpenAI released GPT-5..."
        article.categories = []

        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "classified"
        llm_router.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_keywords_when_no_llm(self):
        from src.pipeline.classify import ClassifyStage

        session = _make_session()

        # Return matching categories from DB
        cat_obj = MagicMock()
        cat_obj.id = 1
        cat_obj.name = "AI Models, Research & Benchmarks"

        cat_result = MagicMock()
        cat_result.scalars.return_value.all.return_value = [cat_obj]

        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(side_effect=[cat_result, no_match])

        # No LLM router — should use keyword fallback
        stage = ClassifyStage(session=session, llm_router=None)

        article = MagicMock()
        article.id = 1
        article.title = "GPT-5 benchmark results are impressive"
        article.markdown_content = "The new GPT model achieves state-of-the-art results."

        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "classified"

    @pytest.mark.asyncio
    async def test_falls_back_to_keywords_on_llm_error(self):
        from src.pipeline.classify import ClassifyStage

        session = _make_session()

        # Simulate categories lookup returning nothing (simplifies assertions)
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=empty_result)

        llm_router = AsyncMock()
        llm_router.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

        stage = ClassifyStage(session=session, llm_router=llm_router)

        article = MagicMock()
        article.id = 1
        article.title = "NVIDIA GPU shortage"
        article.markdown_content = "NVIDIA data center demand continues to grow."

        result = await stage.process(article)

        # Should not crash — returns the article after fallback
        assert result is not None
        assert article.pipeline_status == "classified"


# ===========================================================================
# ScoreStage tests
# ===========================================================================


class TestScoreStage:
    @pytest.mark.asyncio
    async def test_keyword_scoring(self):
        from src.pipeline.score import ScoreStage

        session = _make_session()
        stage = ScoreStage(session=session, llm_router=None)

        article = MagicMock()
        article.id = 1
        article.title = "GPT-5 is a breakthrough"
        article.markdown_content = "OpenAI acquired a security vulnerability scanner."
        article.source = None

        result = await stage.process(article)

        assert result is not None
        # "GPT-5" + "breakthrough" + "acquired" = 3 keyword hits
        # source priority (no source) = 1
        # no LLM = 0
        # total should be 3 + 1 + 0 = 4
        assert article.importance_score == 4
        assert article.pipeline_status == "scored"

    @pytest.mark.asyncio
    async def test_source_priority_included(self):
        from src.pipeline.score import ScoreStage

        session = _make_session()
        stage = ScoreStage(session=session, llm_router=None)

        source = MagicMock()
        source.priority = 3

        article = MagicMock()
        article.id = 1
        article.title = "Regular news"
        article.markdown_content = "Nothing special."
        article.source = source

        result = await stage.process(article)

        assert result is not None
        # 0 keyword hits + 3 source priority + 0 LLM = 3
        assert article.importance_score == 3

    @pytest.mark.asyncio
    async def test_llm_scoring(self):
        from src.pipeline.score import ScoreStage

        session = _make_session()
        llm_router = _make_llm_router(
            {"score": json.dumps({"score": 10, "reason": "Extremely important"})}
        )
        stage = ScoreStage(session=session, llm_router=llm_router)

        article = MagicMock()
        article.id = 1
        article.title = "Regular news"
        article.markdown_content = "Nothing keyword-special."
        article.source = None

        result = await stage.process(article)

        assert result is not None
        # 0 keywords + 1 source default + 4 LLM (score 10 -> normalised 4) = 5
        assert article.importance_score == 5

    @pytest.mark.asyncio
    async def test_breaking_news_threshold(self):
        from src.pipeline.score import ScoreStage

        session = _make_session()
        # LLM returns max score
        llm_router = _make_llm_router(
            {"score": json.dumps({"score": 10, "reason": "Huge"})}
        )
        stage = ScoreStage(session=session, llm_router=llm_router)

        source = MagicMock()
        source.priority = 3

        article = MagicMock()
        article.id = 1
        article.title = "GPT-5 breakthrough acquired"
        article.markdown_content = "Security vulnerability found in GPT-5."
        article.source = source

        result = await stage.process(article)

        assert result is not None
        # 3 keywords + 3 source + 4 LLM = 10 (capped at 10)
        assert article.importance_score == 10
        assert article.is_breaking is True

    @pytest.mark.asyncio
    async def test_score_capped_at_ten(self):
        from src.pipeline.score import ScoreStage

        session = _make_session()
        llm_router = _make_llm_router(
            {"score": json.dumps({"score": 10, "reason": "Max"})}
        )
        stage = ScoreStage(session=session, llm_router=llm_router)

        source = MagicMock()
        source.priority = 3

        article = MagicMock()
        article.id = 1
        article.title = "GPT-6 shutdown banned breakthrough"
        article.markdown_content = "world first acquisition security vulnerability"
        article.source = source

        result = await stage.process(article)

        assert result is not None
        assert article.importance_score <= 10


# ===========================================================================
# SummarizeStage tests
# ===========================================================================


class TestSummarizeStage:
    @pytest.mark.asyncio
    async def test_summarizes_via_llm(self):
        from src.pipeline.summarize import SummarizeStage

        session = _make_session()
        llm_router = _make_llm_router()
        stage = SummarizeStage(session=session, llm_router=llm_router)

        article = MagicMock()
        article.id = 1
        article.title = "GPT-5 Released"
        article.markdown_content = "OpenAI released GPT-5, a major advancement."
        article.raw_content = "<p>OpenAI released GPT-5.</p>"
        article.summaries = []

        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "summarized"
        # Verify a Summary was added
        added_objects = [
            call.args[0] for call in session.add.call_args_list
        ]
        from src.models.summary import Summary

        summaries = [obj for obj in added_objects if isinstance(obj, Summary)]
        assert len(summaries) == 1
        assert summaries[0].headline == "GPT-5 Launches"
        assert summaries[0].llm_provider == "openai"

    @pytest.mark.asyncio
    async def test_fallback_summary_when_no_llm(self):
        from src.pipeline.summarize import SummarizeStage

        session = _make_session()
        stage = SummarizeStage(session=session, llm_router=None)

        article = MagicMock()
        article.id = 1
        article.title = "GPT-5 Released"
        article.markdown_content = "OpenAI released GPT-5. It is very good. Testing shows improvements."
        article.raw_content = "<p>Test</p>"

        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "summarized"
        added = [call.args[0] for call in session.add.call_args_list]
        from src.models.summary import Summary

        summaries = [obj for obj in added if isinstance(obj, Summary)]
        assert len(summaries) == 1
        assert summaries[0].llm_provider == "fallback"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self):
        from src.pipeline.summarize import SummarizeStage

        session = _make_session()
        llm_router = AsyncMock()
        llm_router.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

        stage = SummarizeStage(session=session, llm_router=llm_router)

        article = MagicMock()
        article.id = 1
        article.title = "Some Article"
        article.markdown_content = "Content here. Second sentence. Third sentence."
        article.raw_content = ""

        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "summarized"


# ===========================================================================
# RouteStage tests
# ===========================================================================


class TestRouteStage:
    @pytest.mark.asyncio
    async def test_routes_non_breaking_article(self):
        from src.pipeline.route import RouteStage

        session = _make_session()
        stage = RouteStage(session=session)

        article = MagicMock()
        article.id = 1
        article.is_breaking = False
        article.categories = []

        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "routed"
        # Should have added a PostLog
        added = [call.args[0] for call in session.add.call_args_list]
        from src.models.post_log import PostLog

        logs = [obj for obj in added if isinstance(obj, PostLog)]
        assert len(logs) == 1
        assert logs[0].post_type == "digest"

    @pytest.mark.asyncio
    async def test_routes_breaking_article_dispatches_alert(self):
        from src.pipeline.route import RouteStage

        session = _make_session()
        stage = RouteStage(session=session)

        article = MagicMock()
        article.id = 1
        article.is_breaking = True
        article.categories = []

        # Patch the celery import that happens inside _dispatch_breaking_alert
        mock_celery_app = MagicMock()
        mock_celery_module = MagicMock()
        mock_celery_module.current_app = mock_celery_app
        with patch.dict("sys.modules", {"celery": mock_celery_module}):
            result = await stage.process(article)

        assert result is not None
        mock_celery_app.send_task.assert_called_once()
        added = [call.args[0] for call in session.add.call_args_list]
        from src.models.post_log import PostLog

        logs = [obj for obj in added if isinstance(obj, PostLog)]
        assert len(logs) == 1
        assert logs[0].post_type == "alert"

    @pytest.mark.asyncio
    async def test_notifies_subscribers(self):
        from src.pipeline.route import RouteStage

        session = _make_session()

        # Set up subscriptions result
        sub1 = MagicMock()
        sub1.teams_user_id = "user_1"
        sub2 = MagicMock()
        sub2.teams_user_id = "user_2"

        sub_result = MagicMock()
        sub_result.scalars.return_value.all.return_value = [sub1, sub2]
        session.execute = AsyncMock(return_value=sub_result)

        stage = RouteStage(session=session)

        cat = MagicMock()
        cat.id = 1
        article = MagicMock()
        article.id = 1
        article.is_breaking = False
        article.categories = [cat]

        mock_celery_app = MagicMock()
        mock_celery_module = MagicMock()
        mock_celery_module.current_app = mock_celery_app
        with patch.dict("sys.modules", {"celery": mock_celery_module}):
            result = await stage.process(article)

        assert result is not None

    @pytest.mark.asyncio
    async def test_celery_unavailable_does_not_crash(self):
        """If Celery is not installed/configured, the stage should still succeed."""
        from src.pipeline.route import RouteStage

        session = _make_session()
        stage = RouteStage(session=session)

        article = MagicMock()
        article.id = 1
        article.is_breaking = True
        article.categories = []

        # The import of celery will fail in the route stage but it catches the error
        result = await stage.process(article)

        assert result is not None
        assert article.pipeline_status == "routed"


# ===========================================================================
# Orchestrator tests
# ===========================================================================


class TestArticlePipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        from src.pipeline.orchestrator import ArticlePipeline

        session = _make_session()
        llm_router = _make_llm_router()

        pipeline = ArticlePipeline(session=session, llm_router=llm_router)
        raw = _raw_article()

        article = await pipeline.process(raw)

        assert article is not None
        assert article.pipeline_status == "routed"
        session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_stops_on_duplicate(self):
        from src.pipeline.orchestrator import ArticlePipeline

        session = _make_session()

        # Make dedup stage detect a duplicate (first execute returns match)
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = 99
        session.execute = AsyncMock(return_value=dup_result)

        pipeline = ArticlePipeline(session=session, llm_router=None)
        raw = _raw_article()

        article = await pipeline.process(raw)

        # Pipeline should halt at dedup — return None
        assert article is None

    @pytest.mark.asyncio
    async def test_error_in_stage_does_not_crash(self):
        from src.pipeline.orchestrator import ArticlePipeline

        session = _make_session()

        # Make the classify LLM call explode
        bad_router = AsyncMock()
        bad_router.generate = AsyncMock(side_effect=RuntimeError("Kaboom"))

        pipeline = ArticlePipeline(session=session, llm_router=bad_router)

        # Override classify stage to raise
        original_process = pipeline._stages[1].process  # ClassifyStage

        async def _explode(article):
            raise RuntimeError("Stage explosion")

        pipeline._stages[1].process = _explode

        raw = _raw_article()
        article = await pipeline.process(raw)

        # Should return None (error) but not crash
        assert article is None

    @pytest.mark.asyncio
    async def test_batch_processes_multiple_articles(self):
        from src.pipeline.orchestrator import ArticlePipeline

        session = _make_session()
        llm_router = _make_llm_router()

        pipeline = ArticlePipeline(session=session, llm_router=llm_router)
        articles = [
            _raw_article(url="https://example.com/a1", title="Article 1"),
            _raw_article(url="https://example.com/a2", title="Article 2"),
            _raw_article(url="https://example.com/a3", title="Article 3"),
        ]

        results = await pipeline.process_batch(articles)

        # All three should succeed (no duplicates in mocked DB)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_batch_one_failure_does_not_block_others(self):
        from src.pipeline.orchestrator import ArticlePipeline

        session = _make_session()
        pipeline = ArticlePipeline(session=session, llm_router=None)

        # Make the second article fail during ingest
        call_count = 0
        original_ingest = pipeline._ingest.process

        async def _sometimes_fail(raw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Ingest failure")
            return await original_ingest(raw)

        pipeline._ingest.process = _sometimes_fail

        articles = [
            _raw_article(url="https://example.com/ok1", title="OK 1"),
            _raw_article(url="https://example.com/fail", title="Will Fail"),
            _raw_article(url="https://example.com/ok2", title="OK 2"),
        ]

        results = await pipeline.process_batch(articles)

        # Second article should fail, but others succeed
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_pipeline_without_llm_router(self):
        """Pipeline should work end-to-end using keyword fallbacks when no LLM is available."""
        from src.pipeline.orchestrator import ArticlePipeline

        session = _make_session()
        pipeline = ArticlePipeline(session=session, llm_router=None)

        raw = _raw_article()
        article = await pipeline.process(raw)

        assert article is not None
        assert article.pipeline_status == "routed"
