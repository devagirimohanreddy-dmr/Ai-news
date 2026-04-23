"""Integration tests: Pipeline -> Teams notification flow.

Tests that the pipeline correctly triggers alerts for breaking news,
queues normal articles for digest, groups articles by category in digests,
and notifies subscribed users.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock, call

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.models.post_log import PostLog
from src.models.subscription import Subscription
from src.models.summary import Summary
from src.bot.commands.digest import handle_digest
from src.scrapers.base import RawArticle

from tests.conftest import ArticlePipeline, make_raw_article, make_mock_llm_router, FakeLLMResponse


# ---------------------------------------------------------------------------
# Test 1: Breaking article (score >= 8) triggers alert task dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breaking_article_triggers_alert(db_session: AsyncSession, seeded_categories):
    """An article scoring >= BREAKING_NEWS_THRESHOLD should dispatch a Celery alert task."""
    # LLM returns a high score (10 -> normalises to 4) plus keyword "breakthrough" (+1 keyword)
    # and default source priority of 1 -> total = 1 + 1 + 4 = 6 ... we need >= 8
    # Use keywords "GPT-5" and "breakthrough" and "acquired" for +3 keyword score
    # source priority default=1, LLM score 10 -> 4, keyword=3 -> total=8
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["AI Models, Research & Benchmarks"]}
            ),
            "score": json.dumps({"score": 10, "reason": "Extremely significant"}),
            "summarize": json.dumps(
                {
                    "summary": "A breakthrough in AI: GPT-5 has been acquired.",
                    "headline": "GPT-5 Acquired",
                }
            ),
        }
    )
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="GPT-5 acquired: breakthrough in AI",
        url="https://example.com/breaking-gpt5-acquisition",
        raw_content="GPT-5 acquired in a major breakthrough deal.",
    )

    # Patch Celery's send_task so it doesn't need a real broker
    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        article = await pipeline.process(raw)

    assert article is not None
    assert article.is_breaking is True
    assert article.importance_score >= 8

    # Verify alert PostLog was created
    result = await db_session.execute(
        select(PostLog).where(
            PostLog.article_id == article.id,
            PostLog.post_type == "alert",
        )
    )
    alert_log = result.scalar_one_or_none()
    assert alert_log is not None
    assert alert_log.status == "pending"


# ---------------------------------------------------------------------------
# Test 2: Normal articles queue for digest (not alert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_article_queued_for_digest(db_session: AsyncSession, seeded_categories):
    """An article with score < 8 should be routed as 'digest' not 'alert'."""
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["Learning & Resources"]}
            ),
            "score": json.dumps({"score": 3, "reason": "Standard tutorial content"}),
            "summarize": json.dumps(
                {
                    "summary": "A tutorial on fine-tuning language models.",
                    "headline": "Fine-Tuning Tutorial",
                }
            ),
        }
    )
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="Tutorial: How to Fine-Tune LLMs",
        url="https://example.com/fine-tune-tutorial",
        raw_content="A comprehensive tutorial on fine-tuning large language models.",
    )

    article = await pipeline.process(raw)

    assert article is not None
    assert article.is_breaking is False
    assert article.pipeline_status == "routed"

    # PostLog should be "digest" type, not "alert"
    result = await db_session.execute(
        select(PostLog).where(PostLog.article_id == article.id)
    )
    post_log = result.scalar_one()
    assert post_log.post_type == "digest"


# ---------------------------------------------------------------------------
# Test 3: Digest generation groups articles by category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_digest_groups_articles_by_category(
    db_session: AsyncSession, sample_articles, seeded_categories
):
    """handle_digest should return articles grouped by their categories."""
    # sample_articles fixture provides 3 articles in categories:
    #   - "AI Models, Research & Benchmarks"
    #   - "AI Engineering & Developer Tools"
    #   - "AI Policy, Safety & Governance"

    result = await handle_digest(db_session, "now")

    assert "card" in result
    assert result["card"] is not None

    # The text should mention the article count
    assert "3" in result["text"]

    # Inspect the digest card data — the card body should contain category sections
    card_body = result["card"]["body"]

    # Find the "By Category" header and the category containers
    body_texts = []
    for element in card_body:
        if element.get("type") == "TextBlock":
            body_texts.append(element.get("text", ""))
        elif element.get("type") == "Container":
            for item in element.get("items", []):
                if item.get("type") == "TextBlock":
                    body_texts.append(item.get("text", ""))

    # Should mention categories from sample articles
    all_text = " ".join(body_texts)
    assert "AI Models" in all_text or "Research" in all_text
    assert "Policy" in all_text or "Governance" in all_text or "Engineering" in all_text


# ---------------------------------------------------------------------------
# Test 4: Subscriber matching sends notifications to subscribed users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_notifications_dispatched(
    db_session: AsyncSession, seeded_categories
):
    """When an article is classified into a category, subscribed users get notifications."""
    # Create subscriptions for two users in "AI Models, Research & Benchmarks"
    ai_models_cat = seeded_categories[0]  # "AI Models, Research & Benchmarks"
    sub1 = Subscription(teams_user_id="user-alice", category_id=ai_models_cat.id)
    sub2 = Subscription(teams_user_id="user-bob", category_id=ai_models_cat.id)
    db_session.add(sub1)
    db_session.add(sub2)
    await db_session.flush()

    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["AI Models, Research & Benchmarks"]}
            ),
            "score": json.dumps({"score": 5, "reason": "Moderate importance"}),
            "summarize": json.dumps(
                {
                    "summary": "New model benchmarks released.",
                    "headline": "Benchmark Results",
                }
            ),
        }
    )
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="New AI Benchmark Results Released",
        url="https://example.com/benchmark-results",
        raw_content="Latest benchmark results for AI models show improvements.",
    )

    # Patch Celery send_task to capture calls
    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        article = await pipeline.process(raw)

        assert article is not None

        # Verify that send_task was called for subscriber notifications
        subscriber_calls = [
            c for c in mock_celery.send_task.call_args_list
            if "send_subscriber_notification" in str(c)
        ]
        # Should have 2 notification calls (one per subscribed user)
        assert len(subscriber_calls) == 2

        # Verify user IDs in the calls
        notified_users = set()
        for c in subscriber_calls:
            kwargs = c.kwargs.get("kwargs", {}) if c.kwargs else {}
            if not kwargs:
                # Try positional args
                kwargs = c[1].get("kwargs", {})
            notified_users.add(kwargs.get("teams_user_id"))

        assert "user-alice" in notified_users
        assert "user-bob" in notified_users


# ---------------------------------------------------------------------------
# Test 5: User subscribed to multiple matching categories gets one notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_deduplication(
    db_session: AsyncSession, seeded_categories
):
    """A user subscribed to two categories matching the same article gets only one notification."""
    ai_models_cat = seeded_categories[0]  # "AI Models, Research & Benchmarks"
    opensource_cat = seeded_categories[2]  # "Open Source AI Releases"

    # Subscribe user to both categories
    sub1 = Subscription(teams_user_id="user-charlie", category_id=ai_models_cat.id)
    sub2 = Subscription(teams_user_id="user-charlie", category_id=opensource_cat.id)
    db_session.add(sub1)
    db_session.add(sub2)
    await db_session.flush()

    # LLM classifies into both categories
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["AI Models, Research & Benchmarks", "Open Source AI Releases"]}
            ),
            "score": json.dumps({"score": 6, "reason": "Interesting"}),
            "summarize": json.dumps(
                {
                    "summary": "Open source model release with benchmarks.",
                    "headline": "Open Source Model",
                }
            ),
        }
    )
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="New Open Source Model with Benchmarks",
        url="https://example.com/open-model-bench",
        raw_content="An open source AI model release including benchmarks.",
    )

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        article = await pipeline.process(raw)

        assert article is not None

        # Count subscriber notification calls (should be exactly 1 for charlie)
        subscriber_calls = [
            c for c in mock_celery.send_task.call_args_list
            if "send_subscriber_notification" in str(c)
        ]
        assert len(subscriber_calls) == 1


# ---------------------------------------------------------------------------
# Test 6: No subscribers -> no notification tasks dispatched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_subscribers_no_notifications(
    db_session: AsyncSession, seeded_categories
):
    """When no users are subscribed to a category, no notification tasks should be dispatched."""
    llm_router = make_mock_llm_router(
        responses={
            "classify": json.dumps(
                {"categories": ["Learning & Resources"]}
            ),
            "score": json.dumps({"score": 3, "reason": "Moderate"}),
            "summarize": json.dumps(
                {
                    "summary": "A learning resource.",
                    "headline": "Learning Resource",
                }
            ),
        }
    )
    pipeline = ArticlePipeline(session=db_session, llm_router=llm_router)

    raw = make_raw_article(
        title="New AI Tutorial Published",
        url="https://example.com/ai-tutorial",
        raw_content="A comprehensive AI tutorial for beginners.",
    )

    with patch("celery.current_app") as mock_celery:
        mock_celery.send_task = MagicMock()
        article = await pipeline.process(raw)

        assert article is not None

        # No subscriber notification calls expected
        subscriber_calls = [
            c for c in mock_celery.send_task.call_args_list
            if "send_subscriber_notification" in str(c)
        ]
        assert len(subscriber_calls) == 0
