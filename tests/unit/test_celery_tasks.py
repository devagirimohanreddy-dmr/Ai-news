"""Tests for Celery scheduler tasks.

All database access, scrapers, and pipeline calls are mocked so these run
as fast unit tests without external dependencies.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(**overrides) -> MagicMock:
    """Create a mock Source with sensible defaults."""
    source = MagicMock()
    source.id = overrides.get("id", 1)
    source.name = overrides.get("name", "TechCrunch")
    source.url = overrides.get("url", "https://techcrunch.com/feed/")
    source.scraper_type = overrides.get("scraper_type", "rss")
    source.config_json = overrides.get("config_json", None)
    source.enabled = overrides.get("enabled", True)
    source.last_scraped_at = overrides.get("last_scraped_at", None)
    source.error_count = overrides.get("error_count", 0)
    return source


def _make_article(**overrides) -> MagicMock:
    """Create a mock Article with sensible defaults."""
    article = MagicMock()
    article.id = overrides.get("id", 1)
    article.title = overrides.get("title", "GPT-5 Released")
    article.url = overrides.get("url", "https://example.com/gpt5")
    article.importance_score = overrides.get("importance_score", 7)
    article.is_breaking = overrides.get("is_breaking", False)
    article.pipeline_status = overrides.get("pipeline_status", "routed")
    article.created_at = overrides.get(
        "created_at", datetime.now(timezone.utc) - timedelta(hours=2)
    )

    # Summaries
    summary = MagicMock()
    summary.headline = overrides.get("headline", "GPT-5 Launches")
    summary.summary_text = overrides.get("summary_text", "OpenAI released GPT-5.")
    summary.created_at = datetime.now(timezone.utc)
    article.summaries = overrides.get("summaries", [summary])

    # Categories
    cat = MagicMock()
    cat.name = overrides.get("category_name", "AI Models, Research & Benchmarks")
    article.categories = overrides.get("categories", [cat])

    return article


def _mock_session_factory(session: AsyncMock) -> MagicMock:
    """Wrap a mock session in a factory that works with `async with`."""
    factory = MagicMock()

    class _AsyncCtx:
        async def __aenter__(self_):
            return session

        async def __aexit__(self_, *args):
            pass

    factory.return_value = _AsyncCtx()
    return factory


# ===========================================================================
# scrape_source tests
# ===========================================================================


class TestScrapeSource:
    @pytest.mark.asyncio
    async def test_scrape_source_success(self):
        """Successful scrape updates last_scraped_at and resets error_count."""
        from src.scheduler.scrape_tasks import _scrape_source_async

        source = _make_source(error_count=3)
        session = AsyncMock()

        # session.execute returns the source
        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        session.execute = AsyncMock(return_value=source_result)
        session.commit = AsyncMock()

        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(return_value=[MagicMock(), MagicMock()])
        mock_scraper.close = AsyncMock()

        mock_pipeline = AsyncMock()
        mock_pipeline.process_batch = AsyncMock(return_value=[MagicMock()])

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory), \
             patch("src.scheduler.scrape_tasks.ScraperRegistry") as mock_registry, \
             patch("src.scheduler.scrape_tasks.ArticlePipeline", return_value=mock_pipeline):

            mock_registry.get.return_value = mock_scraper

            result = await _scrape_source_async(1)

        assert result["status"] == "success"
        assert result["articles"] == 1
        assert result["raw_count"] == 2
        assert source.error_count == 0
        assert source.last_scraped_at is not None
        mock_scraper.close.assert_awaited_once()
        session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_scrape_source_not_found(self):
        """Source not in DB returns not_found status."""
        from src.scheduler.scrape_tasks import _scrape_source_async

        session = AsyncMock()
        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=source_result)

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory):
            result = await _scrape_source_async(999)

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_scrape_source_disabled(self):
        """Disabled source is skipped."""
        from src.scheduler.scrape_tasks import _scrape_source_async

        source = _make_source(enabled=False)
        session = AsyncMock()
        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        session.execute = AsyncMock(return_value=source_result)

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory):
            result = await _scrape_source_async(1)

        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_scrape_source_error_increments_count(self):
        """Scraper failure increments source.error_count."""
        from src.scheduler.scrape_tasks import _scrape_source_async

        source = _make_source(error_count=1)
        session = AsyncMock()

        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        session.execute = AsyncMock(return_value=source_result)
        session.commit = AsyncMock()

        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(side_effect=RuntimeError("Connection failed"))
        mock_scraper.close = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory), \
             patch("src.scheduler.scrape_tasks.ScraperRegistry") as mock_registry:

            mock_registry.get.return_value = mock_scraper

            result = await _scrape_source_async(1)

        assert result["status"] == "error"
        assert source.error_count == 2
        mock_scraper.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scrape_source_closes_scraper_on_error(self):
        """Scraper.close() is always called, even on failure."""
        from src.scheduler.scrape_tasks import _scrape_source_async

        source = _make_source()
        session = AsyncMock()

        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        session.execute = AsyncMock(return_value=source_result)
        session.commit = AsyncMock()

        mock_scraper = AsyncMock()
        mock_scraper.scrape = AsyncMock(side_effect=ValueError("Bad data"))
        mock_scraper.close = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory), \
             patch("src.scheduler.scrape_tasks.ScraperRegistry") as mock_registry:

            mock_registry.get.return_value = mock_scraper
            await _scrape_source_async(1)

        mock_scraper.close.assert_awaited_once()


# ===========================================================================
# scrape_all_sources tests
# ===========================================================================


class TestScrapeAllSources:
    def test_dispatches_subtasks_for_each_source(self):
        """scrape_all_sources dispatches one scrape_source.delay per enabled source."""
        from src.scheduler import scrape_tasks

        # Patch the async helper to return known source IDs.
        with patch.object(
            scrape_tasks, "_scrape_all_sources_async", return_value=[1, 2, 3]
        ) as mock_async, \
             patch("asyncio.run", side_effect=lambda coro: [1, 2, 3]) as mock_run, \
             patch.object(scrape_tasks, "scrape_source") as mock_task:

            # Call the sync Celery task function directly (unwrapped).
            # We need to simulate what happens when Celery calls it.
            mock_task.delay = MagicMock()

            # Patch asyncio.run to return the coroutine result.
            with patch("src.scheduler.scrape_tasks.asyncio") as mock_asyncio_mod:
                mock_asyncio_mod.run.return_value = [1, 2, 3]
                result = scrape_tasks.scrape_all_sources.__wrapped__()

        assert result["dispatched"] == 3
        assert result["source_ids"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_queries_only_enabled_sources(self):
        """_scrape_all_sources_async queries only enabled=True sources."""
        from src.scheduler.scrape_tasks import _scrape_all_sources_async

        session = AsyncMock()

        # Simulate rows returned from select(Source.id).where(enabled==True)
        row1 = (10,)
        row2 = (20,)
        query_result = MagicMock()
        query_result.all.return_value = [row1, row2]
        session.execute = AsyncMock(return_value=query_result)

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory):
            source_ids = await _scrape_all_sources_async()

        assert source_ids == [10, 20]
        session.execute.assert_awaited_once()


# ===========================================================================
# generate_daily_digest tests
# ===========================================================================


class TestGenerateDailyDigest:
    @pytest.mark.asyncio
    async def test_groups_articles_by_category(self):
        """Digest groups articles into their category buckets."""
        from src.scheduler.digest_tasks import _generate_daily_digest_async

        cat_ai = MagicMock()
        cat_ai.name = "AI Models, Research & Benchmarks"
        cat_infra = MagicMock()
        cat_infra.name = "Infrastructure & Hardware"

        article1 = _make_article(id=1, title="GPT-5", importance_score=9, categories=[cat_ai])
        article2 = _make_article(id=2, title="New GPU", importance_score=6, categories=[cat_infra])
        article3 = _make_article(id=3, title="Claude 4", importance_score=8, categories=[cat_ai])

        session = AsyncMock()
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [article1, article2, article3]
        session.execute = AsyncMock(return_value=query_result)
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.digest_tasks.get_session_factory", return_value=factory):
            digest = await _generate_daily_digest_async()

        assert digest["total_articles"] == 3
        assert "AI Models, Research & Benchmarks" in digest["categories"]
        assert "Infrastructure & Hardware" in digest["categories"]
        assert len(digest["categories"]["AI Models, Research & Benchmarks"]) == 2
        assert len(digest["categories"]["Infrastructure & Hardware"]) == 1

    @pytest.mark.asyncio
    async def test_top_stories_ordered_by_score(self):
        """Top stories are the highest-scored articles across all categories."""
        from src.scheduler.digest_tasks import _generate_daily_digest_async

        articles = [
            _make_article(id=i, title=f"Article {i}", importance_score=score)
            for i, score in [(1, 5), (2, 9), (3, 7), (4, 10), (5, 3), (6, 8)]
        ]

        session = AsyncMock()
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = articles
        session.execute = AsyncMock(return_value=query_result)
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.digest_tasks.get_session_factory", return_value=factory):
            digest = await _generate_daily_digest_async()

        top_scores = [s["score"] for s in digest["top_stories"]]
        # Should be sorted descending, max 5
        assert len(top_scores) <= 5
        assert top_scores == sorted(top_scores, reverse=True)
        assert top_scores[0] == 10

    @pytest.mark.asyncio
    async def test_empty_digest_when_no_articles(self):
        """Digest returns empty structure when no articles are found."""
        from src.scheduler.digest_tasks import _generate_daily_digest_async

        session = AsyncMock()
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=query_result)

        factory = _mock_session_factory(session)

        with patch("src.scheduler.digest_tasks.get_session_factory", return_value=factory):
            digest = await _generate_daily_digest_async()

        assert digest["total_articles"] == 0
        assert digest["top_stories"] == []
        assert digest["categories"] == {}

    @pytest.mark.asyncio
    async def test_creates_post_log_entry(self):
        """Digest creates a PostLog entry with post_type='digest'."""
        from src.scheduler.digest_tasks import _generate_daily_digest_async

        article = _make_article()

        session = AsyncMock()
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [article]
        session.execute = AsyncMock(return_value=query_result)
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.digest_tasks.get_session_factory", return_value=factory):
            await _generate_daily_digest_async()

        # Verify PostLog was added.
        session.add.assert_called_once()
        added_obj = session.add.call_args[0][0]
        assert added_obj.post_type == "digest"
        assert added_obj.status == "pending"
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncategorised_articles(self):
        """Articles without categories land in 'Uncategorised'."""
        from src.scheduler.digest_tasks import _generate_daily_digest_async

        article = _make_article(categories=[])

        session = AsyncMock()
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [article]
        session.execute = AsyncMock(return_value=query_result)
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.digest_tasks.get_session_factory", return_value=factory):
            digest = await _generate_daily_digest_async()

        assert "Uncategorised" in digest["categories"]

    def test_generate_daily_digest_dispatches_post(self):
        """generate_daily_digest calls post_digest when articles exist."""
        from src.scheduler import digest_tasks

        digest_data = {
            "date": "2026-04-23",
            "total_articles": 5,
            "top_stories": [{"title": "T", "summary": "S", "url": "U", "score": 9}],
            "categories": {"AI": [{"title": "T"}]},
        }

        with patch("src.scheduler.digest_tasks.asyncio") as mock_asyncio, \
             patch("src.scheduler.digest_tasks.app") as mock_app:

            mock_asyncio.run.return_value = digest_data
            result = digest_tasks.generate_daily_digest.__wrapped__()

        mock_app.send_task.assert_called_once_with(
            "src.scheduler.digest_tasks.post_digest",
            args=[digest_data],
        )
        assert result["total_articles"] == 5

    def test_generate_daily_digest_skips_post_when_empty(self):
        """generate_daily_digest does NOT dispatch post_digest when no articles."""
        from src.scheduler import digest_tasks

        digest_data = {
            "date": "2026-04-23",
            "total_articles": 0,
            "top_stories": [],
            "categories": {},
        }

        with patch("src.scheduler.digest_tasks.asyncio") as mock_asyncio, \
             patch("src.scheduler.digest_tasks.app") as mock_app:

            mock_asyncio.run.return_value = digest_data
            result = digest_tasks.generate_daily_digest.__wrapped__()

        mock_app.send_task.assert_not_called()
        assert result["total_articles"] == 0


# ===========================================================================
# post_breaking_alert tests
# ===========================================================================


class TestPostBreakingAlert:
    @pytest.mark.asyncio
    async def test_creates_post_log(self):
        """Alert creates a PostLog entry with post_type='alert'."""
        from src.scheduler.alert_tasks import _post_breaking_alert_async

        article = _make_article(is_breaking=True, importance_score=9)

        session = AsyncMock()

        # First execute: load article
        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = article

        # Second execute: duplicate check returns None (no existing alert)
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(side_effect=[article_result, dup_result])
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_breaking_alert_async(1)

        assert result["status"] == "pending"
        session.add.assert_called_once()
        added_obj = session.add.call_args[0][0]
        assert added_obj.post_type == "alert"
        assert added_obj.article_id == article.id

    @pytest.mark.asyncio
    async def test_prevents_duplicate_alerts(self):
        """Alert is skipped if one already exists for the article."""
        from src.scheduler.alert_tasks import _post_breaking_alert_async

        article = _make_article(is_breaking=True)

        session = AsyncMock()

        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = article

        # Duplicate check returns an existing PostLog
        existing_log = MagicMock()
        existing_log.id = 42
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = existing_log

        session.execute = AsyncMock(side_effect=[article_result, dup_result])
        session.add = MagicMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_breaking_alert_async(1)

        assert result["status"] == "duplicate"
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_article_not_found(self):
        """Alert for missing article returns not_found status."""
        from src.scheduler.alert_tasks import _post_breaking_alert_async

        session = AsyncMock()
        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=article_result)

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_breaking_alert_async(999)

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_alert_contains_article_data(self):
        """Alert result includes headline, summary, URL, and score."""
        from src.scheduler.alert_tasks import _post_breaking_alert_async

        article = _make_article(
            title="Major Breakthrough",
            importance_score=10,
            headline="Big News",
            summary_text="Something amazing happened.",
            url="https://example.com/big",
        )

        session = AsyncMock()

        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = article

        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(side_effect=[article_result, dup_result])
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_breaking_alert_async(1)

        alert = result["alert"]
        assert alert["headline"] == "Big News"
        assert alert["summary"] == "Something amazing happened."
        assert alert["url"] == "https://example.com/big"
        assert alert["importance_score"] == 10


# ===========================================================================
# post_subscriber_notification tests
# ===========================================================================


class TestPostSubscriberNotification:
    @pytest.mark.asyncio
    async def test_creates_notification_post_log(self):
        """Notification creates PostLog with post_type='user_request'."""
        from src.scheduler.alert_tasks import _post_subscriber_notification_async

        article = _make_article()

        session = AsyncMock()
        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = article
        session.execute = AsyncMock(return_value=article_result)
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_subscriber_notification_async(1, "user_abc")

        assert result["status"] == "pending"
        assert result["teams_user_id"] == "user_abc"
        added_obj = session.add.call_args[0][0]
        assert added_obj.post_type == "user_request"

    @pytest.mark.asyncio
    async def test_article_not_found_for_notification(self):
        """Notification for missing article returns not_found."""
        from src.scheduler.alert_tasks import _post_subscriber_notification_async

        session = AsyncMock()
        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=article_result)

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_subscriber_notification_async(999, "user_abc")

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_notification_contains_user_and_article_data(self):
        """Notification payload includes user ID, headline, and URL."""
        from src.scheduler.alert_tasks import _post_subscriber_notification_async

        article = _make_article(
            title="New Feature",
            headline="Feature Drop",
            summary_text="A cool new feature.",
            url="https://example.com/feature",
        )

        session = AsyncMock()
        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = article
        session.execute = AsyncMock(return_value=article_result)
        session.add = MagicMock()
        session.commit = AsyncMock()

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            result = await _post_subscriber_notification_async(1, "user_xyz")

        notification = result["notification"]
        assert notification["teams_user_id"] == "user_xyz"
        assert notification["headline"] == "Feature Drop"
        assert notification["url"] == "https://example.com/feature"


# ===========================================================================
# Error handling
# ===========================================================================


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_scrape_source_db_error(self):
        """DB error during source lookup propagates up for Celery retry."""
        from src.scheduler.scrape_tasks import _scrape_source_async

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        factory = _mock_session_factory(session)

        with patch("src.scheduler.scrape_tasks.get_session_factory", return_value=factory):
            # The initial DB query is intentionally NOT wrapped in try/except
            # so the error propagates to the Celery task wrapper, which retries.
            with pytest.raises(RuntimeError, match="DB connection lost"):
                await _scrape_source_async(1)

    @pytest.mark.asyncio
    async def test_alert_handles_commit_failure_gracefully(self):
        """Breaking alert handles commit failure."""
        from src.scheduler.alert_tasks import _post_breaking_alert_async

        article = _make_article(is_breaking=True)

        session = AsyncMock()

        article_result = MagicMock()
        article_result.scalar_one_or_none.return_value = article

        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(side_effect=[article_result, dup_result])
        session.add = MagicMock()
        session.commit = AsyncMock(side_effect=RuntimeError("DB write failed"))

        factory = _mock_session_factory(session)

        with patch("src.scheduler.alert_tasks.get_session_factory", return_value=factory):
            # Should propagate (Celery task wrapper will retry).
            with pytest.raises(RuntimeError, match="DB write failed"):
                await _post_breaking_alert_async(1)

    def test_post_digest_logs_without_error(self):
        """post_digest logs the digest data without raising."""
        from src.scheduler.digest_tasks import post_digest

        digest_data = {
            "date": "2026-04-23",
            "total_articles": 3,
            "top_stories": [
                {"title": "Big Story", "score": 10, "summary": "S", "url": "U"},
            ],
            "categories": {"AI": [{"title": "T"}]},
        }

        # Call the unwrapped function directly.
        result = post_digest.__wrapped__(digest_data)

        assert result["status"] == "pending"
        assert result["date"] == "2026-04-23"
