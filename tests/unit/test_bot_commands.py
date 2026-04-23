"""Unit tests for the Teams bot command handlers.

All database access is mocked — these tests exercise the handler logic,
card building, and error handling without requiring a live database.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Card builders (no DB needed)
# ---------------------------------------------------------------------------
from src.bot.cards.article_card import build_article_card
from src.bot.cards.alert_card import build_alert_card
from src.bot.cards.digest_card import build_digest_card
from src.bot.cards.help_card import build_help_card

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
from src.bot.commands.latest import handle_latest
from src.bot.commands.search import handle_search, _sanitize_query
from src.bot.commands.subscribe import handle_subscribe, handle_unsubscribe
from src.bot.commands.summarize import handle_summarize, _validate_url
from src.bot.commands.help import handle_help
from src.bot.commands.digest import handle_digest


# =========================================================================== #
# Helpers / Fixtures                                                           #
# =========================================================================== #


def _fake_category(id_: int = 1, name: str = "AI") -> MagicMock:
    cat = MagicMock()
    cat.id = id_
    cat.name = name
    cat.enabled = True
    return cat


def _fake_summary(text: str = "A summary.", headline: str = "Headline") -> MagicMock:
    s = MagicMock()
    s.summary_text = text
    s.headline = headline
    return s


def _fake_source(name: str = "TechCrunch") -> MagicMock:
    src = MagicMock()
    src.name = name
    return src


def _fake_article(
    id_: int = 1,
    title: str = "Test Article",
    url: str = "https://example.com/article",
    categories: list | None = None,
    summaries: list | None = None,
    source: Any = None,
    importance_score: int = 7,
    published_at: datetime | None = None,
    pipeline_status: str = "routed",
    author: str | None = "Author",
) -> MagicMock:
    a = MagicMock()
    a.id = id_
    a.title = title
    a.url = url
    a.url_hash = hashlib.sha256(url.encode()).hexdigest()
    a.categories = categories if categories is not None else [_fake_category()]
    a.summaries = summaries if summaries is not None else [_fake_summary()]
    a.source = source or _fake_source()
    a.importance_score = importance_score
    a.published_at = published_at or datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    a.pipeline_status = pipeline_status
    a.author = author
    a.created_at = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    return a


def _mock_session_with_articles(articles: list) -> AsyncMock:
    """Create an AsyncMock session whose execute returns the given articles."""
    session = AsyncMock()
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.unique.return_value.all.return_value = articles
    scalars_mock.all.return_value = articles
    result_mock.scalars.return_value = scalars_mock
    result_mock.scalar_one_or_none.return_value = articles[0] if articles else None
    result_mock.all.return_value = articles
    result_mock.fetchall.return_value = articles
    session.execute.return_value = result_mock
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    return session


# =========================================================================== #
# Card builder tests                                                           #
# =========================================================================== #


class TestArticleCard:
    def test_basic_card_structure(self):
        card = build_article_card({
            "title": "Test",
            "url": "https://example.com",
            "summary": "A test summary",
        })
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"
        assert len(card["body"]) >= 2
        assert card["actions"][0]["type"] == "Action.OpenUrl"

    def test_card_without_url(self):
        card = build_article_card({"title": "No URL"})
        assert card["actions"] == []

    def test_card_includes_categories(self):
        card = build_article_card({
            "title": "Test",
            "categories": ["AI", "ML"],
        })
        cat_block = card["body"][1]
        assert "AI" in cat_block["text"]
        assert "ML" in cat_block["text"]


class TestAlertCard:
    def test_alert_card_has_attention_style(self):
        card = build_alert_card({
            "title": "Breaking",
            "url": "https://example.com",
            "summary": "Big news",
        })
        assert card["body"][0]["style"] == "attention"
        assert card["body"][0]["items"][0]["text"] == "BREAKING NEWS"

    def test_alert_card_facts(self):
        card = build_alert_card({
            "title": "Breaking",
            "summary": "Summary",
            "source_name": "CNN",
            "importance_score": 9,
        })
        facts = card["body"][2]["facts"]
        source_fact = next(f for f in facts if f["title"] == "Source")
        assert source_fact["value"] == "CNN"


class TestDigestCard:
    def test_digest_card_structure(self):
        card = build_digest_card({
            "date": "2026-04-23",
            "total_count": 10,
            "top_stories": [
                {"title": "Story 1", "url": "https://example.com/1", "summary": "S1"},
            ],
            "categories": {
                "AI": [{"title": "A1", "url": "https://example.com/a1"}],
            },
        })
        assert card["type"] == "AdaptiveCard"
        header = card["body"][0]
        assert "2026-04-23" in header["text"]

    def test_digest_empty(self):
        card = build_digest_card({"date": "2026-04-23", "total_count": 0})
        assert card["type"] == "AdaptiveCard"


class TestHelpCard:
    def test_help_card_lists_commands(self):
        card = build_help_card()
        assert card["type"] == "AdaptiveCard"
        # Should have the title, subtitle, command rows, and a tip
        assert len(card["body"]) >= 3


# =========================================================================== #
# Command handler tests                                                        #
# =========================================================================== #


class TestLatestCommand:
    @pytest.mark.asyncio
    async def test_latest_returns_articles(self):
        articles = [_fake_article(id_=i, title=f"Article {i}") for i in range(3)]
        session = _mock_session_with_articles(articles)

        result = await handle_latest(session, "")
        assert "cards" in result
        assert len(result["cards"]) == 3
        assert result["cards"][0]["type"] == "AdaptiveCard"

    @pytest.mark.asyncio
    async def test_latest_empty(self):
        session = _mock_session_with_articles([])
        result = await handle_latest(session, "")
        assert "No recent articles found" in result["text"]
        assert result["cards"] == []

    @pytest.mark.asyncio
    async def test_latest_with_category_filter(self):
        articles = [_fake_article(categories=[_fake_category(name="AI")])]
        session = _mock_session_with_articles(articles)

        result = await handle_latest(session, "AI")
        assert "cards" in result
        assert len(result["cards"]) == 1

    @pytest.mark.asyncio
    async def test_latest_handles_db_error(self):
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("DB down")

        result = await handle_latest(session, "")
        assert "went wrong" in result["text"]


class TestSearchCommand:
    def test_sanitize_query_removes_special_chars(self):
        assert _sanitize_query("test; DROP TABLE") == "test DROP TABLE"
        assert _sanitize_query("normal query") == "normal query"
        # Hyphens are allowed (common in search terms)
        assert _sanitize_query("machine-learning") == "machine-learning"
        # Semicolons are stripped
        assert ";" not in _sanitize_query("test;injection")

    def test_sanitize_query_truncates_long_input(self):
        long_input = "a" * 300
        assert len(_sanitize_query(long_input)) <= 200

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        session = AsyncMock()
        result = await handle_search(session, "")
        assert "Please provide a search query" in result["text"]

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        # Simulate the two-step query: first raw SQL, then ORM fetch
        fake_row = MagicMock()
        fake_row.id = 1

        article = _fake_article()

        session = AsyncMock()

        # First call: full-text search returns row with id
        fts_result = MagicMock()
        fts_result.fetchall.return_value = [fake_row]

        # Second call: ORM fetch by IDs
        orm_result = MagicMock()
        orm_scalars = MagicMock()
        orm_scalars.unique.return_value.all.return_value = [article]
        orm_result.scalars.return_value = orm_scalars

        session.execute = AsyncMock(side_effect=[fts_result, orm_result])

        result = await handle_search(session, "test query")
        assert "cards" in result
        assert len(result["cards"]) == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        session = AsyncMock()
        fts_result = MagicMock()
        fts_result.fetchall.return_value = []
        session.execute = AsyncMock(return_value=fts_result)

        result = await handle_search(session, "nonexistent topic")
        assert "No articles found" in result["text"]


class TestSubscribeCommand:
    @pytest.mark.asyncio
    async def test_subscribe_no_args_lists_categories(self):
        categories = [_fake_category(id_=1, name="AI"), _fake_category(id_=2, name="Security")]
        session = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = categories
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        result = await handle_subscribe(session, "", "user123")
        assert "Available categories" in result["text"]
        assert "AI" in result["text"]

    @pytest.mark.asyncio
    async def test_subscribe_creates_subscription(self):
        cat = _fake_category(id_=1, name="AI")

        session = AsyncMock()
        call_count = 0

        async def _execute_side_effect(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            result_mock = MagicMock()

            if call_count == 1:
                # _fuzzy_find_category exact match
                result_mock.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                # Check if already subscribed
                result_mock.scalar_one_or_none.return_value = None
            return result_mock

        session.execute = AsyncMock(side_effect=_execute_side_effect)
        session.add = MagicMock()
        session.commit = AsyncMock()

        result = await handle_subscribe(session, "AI", "user123")
        assert "Subscribed to 'AI'" in result["text"]
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_already_subscribed(self):
        cat = _fake_category(id_=1, name="AI")
        existing_sub = MagicMock()

        session = AsyncMock()
        call_count = 0

        async def _execute_side_effect(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            result_mock = MagicMock()

            if call_count == 1:
                result_mock.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                result_mock.scalar_one_or_none.return_value = existing_sub
            return result_mock

        session.execute = AsyncMock(side_effect=_execute_side_effect)

        result = await handle_subscribe(session, "AI", "user123")
        assert "already subscribed" in result["text"]

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_subscription(self):
        cat = _fake_category(id_=1, name="AI")

        session = AsyncMock()
        call_count = 0

        async def _execute_side_effect(stmt, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            result_mock = MagicMock()

            if call_count == 1:
                result_mock.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                result_mock.rowcount = 1
            return result_mock

        session.execute = AsyncMock(side_effect=_execute_side_effect)
        session.commit = AsyncMock()

        result = await handle_unsubscribe(session, "AI", "user123")
        assert "Unsubscribed from 'AI'" in result["text"]


class TestSummarizeCommand:
    def test_validate_url_valid(self):
        assert _validate_url("https://example.com/article") is not None
        assert _validate_url("http://example.com") is not None

    def test_validate_url_invalid(self):
        assert _validate_url("not-a-url") is None
        assert _validate_url("ftp://example.com") is None
        assert _validate_url("") is None

    @pytest.mark.asyncio
    async def test_summarize_no_url(self):
        session = AsyncMock()
        result = await handle_summarize(session, "")
        assert "Please provide a URL" in result["text"]

    @pytest.mark.asyncio
    async def test_summarize_invalid_url(self):
        session = AsyncMock()
        result = await handle_summarize(session, "not-a-url")
        assert "does not look like a valid URL" in result["text"]

    @pytest.mark.asyncio
    async def test_summarize_returns_cached(self):
        article = _fake_article(
            url="https://example.com/cached",
            summaries=[_fake_summary("Cached summary", "Cached headline")],
        )
        article.url_hash = hashlib.sha256(b"https://example.com/cached").hexdigest()

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = article
        session.execute = AsyncMock(return_value=result_mock)

        result = await handle_summarize(session, "https://example.com/cached")
        assert result["card"] is not None
        assert "cached summary" in result["text"].lower()

    @pytest.mark.asyncio
    async def test_summarize_new_url_no_pipeline(self):
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        result = await handle_summarize(session, "https://example.com/new-article")
        assert "pipeline is not available" in result["text"]

    @pytest.mark.asyncio
    async def test_summarize_new_url_with_pipeline(self):
        processed_article = _fake_article(
            url="https://example.com/new",
            summaries=[_fake_summary("New summary")],
        )

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        mock_pipeline = MagicMock()
        mock_pipeline.process = AsyncMock(return_value=processed_article)
        pipeline_factory = MagicMock(return_value=mock_pipeline)

        result = await handle_summarize(
            session, "https://example.com/new", pipeline_factory
        )
        assert result["card"] is not None


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_help_returns_card_and_text(self):
        result = await handle_help()
        assert "card" in result
        assert result["card"]["type"] == "AdaptiveCard"
        assert "/latest" in result["text"]
        assert "/search" in result["text"]
        assert "/subscribe" in result["text"]
        assert "/help" in result["text"]

    @pytest.mark.asyncio
    async def test_help_card_has_all_commands(self):
        result = await handle_help()
        card = result["card"]
        # The body should contain ColumnSets for each command
        column_sets = [b for b in card["body"] if b.get("type") == "ColumnSet"]
        # We have 8 commands listed
        assert len(column_sets) == 8


class TestDigestCommand:
    @pytest.mark.asyncio
    async def test_digest_returns_card(self):
        articles = [
            _fake_article(id_=1, title="Article 1", importance_score=9),
            _fake_article(id_=2, title="Article 2", importance_score=5),
        ]
        session = _mock_session_with_articles(articles)

        result = await handle_digest(session, "")
        assert result["card"] is not None
        assert result["card"]["type"] == "AdaptiveCard"
        assert "2 article(s)" in result["text"]

    @pytest.mark.asyncio
    async def test_digest_empty(self):
        session = _mock_session_with_articles([])
        result = await handle_digest(session, "")
        assert "No articles found" in result["text"]
        assert result["card"] is None

    @pytest.mark.asyncio
    async def test_digest_handles_error(self):
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("DB error")

        result = await handle_digest(session, "")
        assert "went wrong" in result["text"]
