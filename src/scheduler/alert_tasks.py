"""Celery tasks for posting breaking-news alerts and subscriber notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config.celery_app import app
from src.models.article import Article
from src.models.base import get_session_factory
from src.models.post_log import PostLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Breaking-news alerts
# ---------------------------------------------------------------------------


async def _post_breaking_alert_async(article_id: int) -> dict:
    """Core async logic for posting a breaking-news alert."""
    session_factory = get_session_factory()

    async with session_factory() as session:
        # Load article with its summaries.
        result = await session.execute(
            select(Article)
            .options(selectinload(Article.summaries))
            .where(Article.id == article_id)
        )
        article = result.scalar_one_or_none()

        if article is None:
            logger.error("Article id=%s not found for breaking alert", article_id)
            return {"article_id": article_id, "status": "not_found"}

        # Duplicate guard: check if an alert PostLog already exists for this article.
        dup_check = await session.execute(
            select(PostLog).where(
                PostLog.article_id == article_id,
                PostLog.post_type == "alert",
                PostLog.status.in_(["pending", "success"]),
            )
        )
        existing = dup_check.scalar_one_or_none()

        if existing is not None:
            logger.warning(
                "Breaking alert already exists for article id=%s (post_log id=%s), skipping",
                article_id,
                existing.id,
            )
            return {"article_id": article_id, "status": "duplicate"}

        # Build alert data.
        summary_text = ""
        headline = article.title
        if article.summaries:
            best = sorted(article.summaries, key=lambda s: s.created_at, reverse=True)[0]
            summary_text = best.summary_text
            headline = best.headline or article.title

        alert_data = {
            "article_id": article.id,
            "headline": headline,
            "summary": summary_text,
            "url": article.url,
            "importance_score": article.importance_score,
            "posted_at": datetime.now(timezone.utc).isoformat(),
        }

        # Log the alert (Teams posting will be implemented in Phase 6).
        logger.info(
            "BREAKING ALERT: [score=%d] %s — %s",
            article.importance_score,
            headline,
            article.url,
        )

        # Create PostLog entry.
        post_log = PostLog(
            article_id=article.id,
            post_type="alert",
            status="pending",
        )
        session.add(post_log)
        await session.commit()

        return {"article_id": article_id, "status": "pending", "alert": alert_data}


@app.task(name="src.scheduler.alert_tasks.post_breaking_alert", bind=True, max_retries=3)
def post_breaking_alert(self, article_id: int) -> dict:
    """Celery task: post a breaking-news alert for a specific article.

    Loads the article and its summary, checks for duplicate alerts,
    builds the alert payload, and creates a PostLog entry.
    """
    try:
        return asyncio.run(_post_breaking_alert_async(article_id))
    except Exception as exc:
        logger.error(
            "post_breaking_alert failed for article_id=%s: %s",
            article_id, exc, exc_info=True,
        )
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))


# ---------------------------------------------------------------------------
# Subscriber notifications
# ---------------------------------------------------------------------------


async def _post_subscriber_notification_async(
    article_id: int, teams_user_id: str
) -> dict:
    """Core async logic for posting a subscriber notification."""
    session_factory = get_session_factory()

    async with session_factory() as session:
        # Load article with summaries.
        result = await session.execute(
            select(Article)
            .options(selectinload(Article.summaries))
            .where(Article.id == article_id)
        )
        article = result.scalar_one_or_none()

        if article is None:
            logger.error(
                "Article id=%s not found for subscriber notification", article_id,
            )
            return {
                "article_id": article_id,
                "teams_user_id": teams_user_id,
                "status": "not_found",
            }

        # Build notification data.
        summary_text = ""
        headline = article.title
        if article.summaries:
            best = sorted(article.summaries, key=lambda s: s.created_at, reverse=True)[0]
            summary_text = best.summary_text
            headline = best.headline or article.title

        notification_data = {
            "article_id": article.id,
            "teams_user_id": teams_user_id,
            "headline": headline,
            "summary": summary_text,
            "url": article.url,
            "importance_score": article.importance_score,
        }

        # Log the notification (Teams posting will be implemented in Phase 6).
        logger.info(
            "Subscriber notification for user=%s: [score=%d] %s",
            teams_user_id,
            article.importance_score,
            headline,
        )

        # Create PostLog entry.
        post_log = PostLog(
            article_id=article.id,
            post_type="user_request",
            status="pending",
        )
        session.add(post_log)
        await session.commit()

        return {
            "article_id": article_id,
            "teams_user_id": teams_user_id,
            "status": "pending",
            "notification": notification_data,
        }


@app.task(
    name="src.scheduler.alert_tasks.post_subscriber_notification",
    bind=True,
    max_retries=3,
)
def post_subscriber_notification(
    self, article_id: int, teams_user_id: str
) -> dict:
    """Celery task: send a notification to a specific Teams user about an article.

    Loads the article and its summary, builds the notification payload,
    and creates a PostLog entry.
    """
    try:
        return asyncio.run(
            _post_subscriber_notification_async(article_id, teams_user_id)
        )
    except Exception as exc:
        logger.error(
            "post_subscriber_notification failed for article_id=%s user=%s: %s",
            article_id, teams_user_id, exc, exc_info=True,
        )
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))
