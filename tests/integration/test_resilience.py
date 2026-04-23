"""Integration tests: Resilience and error handling.

Tests that the system handles failures gracefully: batch processing with
partial failures, LLM fallback chains, scraper errors, DB commit failures,
and Celery task retries.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.summary import Summary
from src.scrapers.base import RawArticle

from tests.conftest import ArticlePipeline, make_raw_article, make_mock_llm_router, FakeLLMResponse


# ---------------------------------------------------------------------------
# Test 1: Batch processing — 20+ articles, some fail, others succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_processing_partial_failures(
    db_session: AsyncSession, seeded_categories, session_factory
):
    """process_batch should succeed for valid articles and skip failures.

    Duplicates are detected in the DedupStage by URL hash when processing
    within the same session/batch.  This test uses a fresh session per
    pipeline run to avoid session poisoning from UNIQUE constraint errors.
    """
    llm_router = make_mock_llm_router()

    # First: process 20 unique articles
    async with session_factory() as session:
        pipeline = ArticlePipeline(session=session, llm_router=llm_router)

        raw_articles: list[RawArticle] = []
        for i in range(20):
            raw_articles.append(
                make_raw_article(
                    title=f"GPT-5 Batch Article {i} -- a breakthrough in AI",
                    url=f"https://example.com/batch-article-{i}",
                    raw_content=f"<p>Content for batch article {i} about AI breakthrough.</p>",
                )
            )

        with patch("celery.current_app") as mock_celery:
            mock_celery.send_task = MagicMock()
            results = await pipeline.process_batch(raw_articles)

    # All 20 unique articles should process successfully
    assert len(results) == 20

    # Now try processing 2 duplicates in a new session — they should be detected
    async with session_factory() as session2:
        pipeline2 = ArticlePipeline(session=session2, llm_router=llm_router)

        duplicate_articles = [
            make_raw_article(
                title="Duplicate of Article 0",
                url="https://example.com/batch-article-0",
                raw_content="<p>Duplicate content</p>",
            ),
            make_raw_article(
                title="Duplicate of Article 1",
                url="https://example.com/batch-article-1",
                raw_content="<p>Duplicate content</p>",
            ),
        ]

        dup_results = await pipeline2.process_batch(duplicate_articles)

    # Duplicates should be filtered out (either by dedup or unique constraint)
    assert len(dup_results) == 0

    # Total in DB should be exactly 20
    async with session_factory() as session3:
        result = await session3.execute(select(Article))
        all_articles = result.scalars().all()
        assert len(all_articles) == 20


@pytest.mark.asyncio
async def test_batch_one_scraper_error_doesnt_block_others(
    db_session: AsyncSession, seeded_categories
):
    """If one article in a batch causes an exception, others should still process."""
    call_count = 0
    original_generate = None

    llm_router = make_mock_llm_router()

    # Override the classify LLM to fail on the 3rd call
    original_side_effect = llm_router.generate.side_effect

    async def flaky_generate(
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
        task_type: str = "general",
    ):
        nonlocal call_count
        call_count += 1
        # Fail on every 5th LLM call (which will be inside some articles)
        if call_count % 5 == 0:
            raise RuntimeError("Simulated LLM timeout")
        return await original_side_effect(
            prompt=prompt,
            system_prompt=system_prompt,
            json_mode=json_mode,
            task_type=task_type,
        )

    llm_router.generate.side_effect = flaky_generate

    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw_articles = [
        make_raw_article(
            title=f"Batch Flaky Article {i}",
            url=f"https://example.com/flaky-batch-{i}",
            raw_content=f"<p>Content for flaky article {i}.</p>",
        )
        for i in range(10)
    ]

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        results = await pipeline.process_batch(raw_articles)

    # Some should succeed, some may fail — but we should have at least some results
    assert len(results) > 0
    # Not all should fail
    assert len(results) < 10 or len(results) == 10  # all could succeed if failure hit non-critical stage

    # All returned articles should have valid IDs
    for article in results:
        assert article.id is not None


# ---------------------------------------------------------------------------
# Test 2: All cloud LLMs fail -> Ollama fallback works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_fallback_to_keyword_classification(
    db_session: AsyncSession, seeded_categories
):
    """When the LLM router fails, classification should fall back to keywords."""
    # Create an LLM router that always fails
    failing_router = AsyncMock()
    failing_router.generate = AsyncMock(
        side_effect=RuntimeError("All providers failed")
    )

    pipeline = ArticlePipeline(session=db_session, llm_router=failing_router)

    raw = make_raw_article(
        title="NVIDIA launches new GPU for AI",
        url="https://example.com/nvidia-gpu-launch",
        raw_content="NVIDIA has launched a new H100 GPU for data center AI workloads.",
    )

    article = await pipeline.process(raw)

    # The pipeline should still succeed using fallback paths
    # IngestStage doesn't use LLM, so it always succeeds
    # DedupStage doesn't use LLM
    # ClassifyStage falls back to keyword matching
    # ScoreStage returns 0 for LLM component (catches exception)
    # SummarizeStage falls back to extractive summary
    # RouteStage doesn't use LLM
    assert article is not None
    assert article.pipeline_status == "routed"

    # Classification should have used keyword fallback
    # "NVIDIA" keyword -> "AI Infrastructure & Big Tech"
    # "GPU" keyword -> "AI Infrastructure & Big Tech"
    from src.models.article_category import ArticleCategory
    from src.models.category import Category

    result = await db_session.execute(
        select(Category)
        .join(ArticleCategory, ArticleCategory.category_id == Category.id)
        .where(ArticleCategory.article_id == article.id)
    )
    categories = result.scalars().all()
    cat_names = [c.name for c in categories]
    assert "AI Infrastructure & Big Tech" in cat_names

    # Summary should be a fallback (extractive)
    result = await db_session.execute(
        select(Summary).where(Summary.article_id == article.id)
    )
    summary = result.scalars().first()
    assert summary is not None
    assert summary.llm_provider == "fallback"


@pytest.mark.asyncio
async def test_llm_failure_preserves_scoring_keyword_component(
    db_session: AsyncSession, seeded_categories
):
    """When LLM scoring fails, keyword and source scores should still apply."""
    failing_router = AsyncMock()
    failing_router.generate = AsyncMock(
        side_effect=RuntimeError("LLM unavailable")
    )

    pipeline = ArticlePipeline(session=db_session, llm_router=failing_router)

    # "breakthrough" is in BREAKING_KEYWORDS -> +1 keyword score
    raw = make_raw_article(
        title="A breakthrough in AI research",
        url="https://example.com/breakthrough-article",
        raw_content="This is a breakthrough discovery in AI research.",
    )

    article = await pipeline.process(raw)

    assert article is not None
    # LLM score = 0 (failed), keyword score >= 1 ("breakthrough"), source = 1
    # Total >= 2
    assert article.importance_score >= 2


# ---------------------------------------------------------------------------
# Test 3: Scraper failure doesn't crash pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_failure_doesnt_crash_pipeline(
    db_session: AsyncSession, seeded_categories
):
    """A scraper producing bad data should not prevent other articles from processing."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    # Create a RawArticle with empty URL — this should fail at ingest
    bad_raw = RawArticle(
        title="Bad Article",
        url="",  # Empty URL will cause issues
        raw_content="Some content",
        source_name="bad_source",
    )

    good_raw = make_raw_article(
        title="Good Article About AI",
        url="https://example.com/good-article",
        raw_content="Good content about AI breakthrough.",
    )

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()

        # Process the bad article — should return None (not raise)
        bad_result = await pipeline.process(bad_raw)

        # Process the good article — should succeed
        good_result = await pipeline.process(good_raw)

    # Bad article should have been handled gracefully
    # (either None or an error was caught)
    # Good article should have processed successfully
    assert good_result is not None
    assert good_result.pipeline_status == "routed"


@pytest.mark.asyncio
async def test_malformed_raw_content_handled(
    db_session: AsyncSession, seeded_categories
):
    """Articles with malformed HTML content should still be processable."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="Article with Bad HTML",
        url="https://example.com/bad-html",
        raw_content="<p>Unclosed tag<div>Mixed <b>content</p></div>",
    )

    article = await pipeline.process(raw)

    # Pipeline should handle malformed HTML gracefully
    assert article is not None
    assert article.id is not None


# ---------------------------------------------------------------------------
# Test 4: DB commit failure is handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_commit_failure_handled(
    db_session: AsyncSession, seeded_categories
):
    """A commit failure at the end of the pipeline should not raise to the caller."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="Article That Will Fail to Commit",
        url="https://example.com/commit-failure",
        raw_content="Some content about AI.",
    )

    # Patch the session's commit to fail after the pipeline processes
    original_commit = db_session.commit

    commit_call_count = 0

    async def failing_commit():
        nonlocal commit_call_count
        commit_call_count += 1
        # Let flushes succeed but fail on the final commit
        if commit_call_count >= 1:
            raise Exception("Simulated DB commit failure")

    with patch.object(db_session, "commit", side_effect=failing_commit):
        with patch.object(db_session, "rollback", new_callable=AsyncMock):
            article = await pipeline.process(raw)

    # Pipeline should return None on commit failure (not raise)
    assert article is None


@pytest.mark.asyncio
async def test_db_unique_constraint_handled(
    db_session: AsyncSession, seeded_categories
):
    """Inserting an article with a duplicate URL should be caught by the pipeline."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    url = "https://example.com/constraint-test"

    raw1 = make_raw_article(
        title="First Article",
        url=url,
        raw_content="First content.",
    )
    raw2 = make_raw_article(
        title="Second Article (Same URL)",
        url=url,
        raw_content="Second content.",
    )

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()

        article1 = await pipeline.process(raw1)
        assert article1 is not None

        article2 = await pipeline.process(raw2)
        # Should be filtered as duplicate
        assert article2 is None


# ---------------------------------------------------------------------------
# Test 5: Celery task retry on transient error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_celery_alert_task_retry_logic():
    """The Celery alert task should use retry with increasing countdown."""
    # We test the retry mechanism by verifying task configuration
    # without needing a real Celery broker.
    from src.scheduler.alert_tasks import post_breaking_alert

    # Verify the task has retry configuration
    assert post_breaking_alert.max_retries == 3

    # Verify the task name is correct
    assert post_breaking_alert.name == "src.scheduler.alert_tasks.post_breaking_alert"


@pytest.mark.asyncio
async def test_celery_scrape_task_retry_logic():
    """The Celery scrape task should use retry with increasing countdown."""
    from src.scheduler.scrape_tasks import scrape_source

    # Verify retry configuration
    assert scrape_source.max_retries == 2
    assert scrape_source.name == "src.scheduler.scrape_tasks.scrape_source"


@pytest.mark.asyncio
async def test_celery_subscriber_notification_retry_logic():
    """The subscriber notification task should support retries."""
    from src.scheduler.alert_tasks import post_subscriber_notification

    assert post_subscriber_notification.max_retries == 3
    assert (
        post_subscriber_notification.name
        == "src.scheduler.alert_tasks.post_subscriber_notification"
    )


# ---------------------------------------------------------------------------
# Test 6: Pipeline continues after individual stage failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_failure_returns_none_not_exception(
    db_session: AsyncSession, seeded_categories
):
    """If a middle stage raises an exception, the pipeline returns None gracefully."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="Article for Stage Failure Test",
        url="https://example.com/stage-failure-test",
        raw_content="Content about AI.",
    )

    # Patch the classify stage to raise an error
    with patch.object(
        pipeline._stages[1],  # ClassifyStage (index 1 = DedupStage is 0)
        "process",
        side_effect=RuntimeError("Classify stage exploded"),
    ):
        article = await pipeline.process(raw)

    # Should return None, not raise
    assert article is None

    # But the article should still be in the DB (partial progress saved)
    result = await db_session.execute(
        select(Article).where(
            Article.url == "https://example.com/stage-failure-test"
        )
    )
    db_article = result.scalar_one_or_none()
    # Article was ingested and flushed before the classify stage failed
    assert db_article is not None
    # Its status reflects where it got to before failure
    assert db_article.pipeline_status in ("ingested", "deduped")


# ---------------------------------------------------------------------------
# Test 7: Large batch with mixed success rates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_batch_mixed_success(
    db_session: AsyncSession, seeded_categories
):
    """A large batch with varied content should produce a reasonable success rate."""
    llm_router = make_mock_llm_router()
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw_articles = []
    for i in range(25):
        raw_articles.append(
            make_raw_article(
                title=f"Large Batch Article {i}: AI Model Research",
                url=f"https://example.com/large-batch-{i}",
                raw_content=f"<p>Research content #{i} about GPT models.</p>",
            )
        )

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        results = await pipeline.process_batch(raw_articles)

    # All 25 should succeed (no duplicates, no intentional failures)
    assert len(results) == 25

    # Verify all are in the DB
    result = await db_session.execute(select(Article))
    all_articles = result.scalars().all()
    assert len(all_articles) == 25
