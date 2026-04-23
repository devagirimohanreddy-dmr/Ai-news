"""RouteStage — dispatches articles to alerts, digest queues, and subscriber notifications."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.post_log import PostLog
from src.models.subscription import Subscription
from src.pipeline.base import PipelineStage

logger = logging.getLogger(__name__)


class RouteStage(PipelineStage):
    """Sixth (final) pipeline stage: route the processed article.

    Responsibilities:

    1. **Breaking-news alert** — if ``article.is_breaking`` is ``True``,
       dispatch a Celery task for immediate notification.  Uses
       ``celery.current_app.send_task`` for loose coupling (the Celery
       worker defines the actual task implementation).
    2. **Digest queue** — mark the article as routed so the periodic digest
       task can pick it up by querying ``pipeline_status = 'routed'`` within
       the target time window.
    3. **Subscriber notifications** — look up users subscribed to the
       article's categories and queue a notification task for each.
    4. **PostLog** — create an audit trail record.
    """

    def __init__(self, session: AsyncSession, **kwargs):
        self.session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, article: Article) -> Article | None:
        # 1. Breaking-news alert
        if article.is_breaking:
            await self._dispatch_breaking_alert(article)

        # 2. Digest queue — article will be picked up by the digest task
        #    based on pipeline_status and created_at / published_at.

        # 3. Subscriber notifications
        await self._notify_subscribers(article)

        # 4. Create PostLog record
        post_type = "alert" if article.is_breaking else "digest"
        post_log = PostLog(
            article_id=article.id,
            post_type=post_type,
            status="pending",
        )
        self.session.add(post_log)

        article.pipeline_status = "routed"
        await self.session.flush()

        logger.info(
            "Article id=%s routed (breaking=%s, post_type=%s)",
            article.id,
            article.is_breaking,
            post_type,
        )
        return article

    # ------------------------------------------------------------------
    # Breaking-news dispatch
    # ------------------------------------------------------------------

    async def _dispatch_breaking_alert(self, article: Article) -> None:
        """Send a Celery task for immediate breaking-news notification.

        Uses ``send_task`` so the pipeline code does not depend on the
        worker module at import time.
        """
        try:
            from celery import current_app as celery_app

            celery_app.send_task(
                "src.tasks.notify.send_breaking_alert",
                kwargs={"article_id": article.id},
                queue="alerts",
            )
            logger.info(
                "Dispatched breaking alert task for article id=%s",
                article.id,
            )
        except Exception:
            # Celery may not be running in test / dev — log and move on.
            logger.warning(
                "Failed to dispatch breaking alert for article id=%s; "
                "Celery may not be configured",
                article.id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Subscriber notifications
    # ------------------------------------------------------------------

    async def _notify_subscribers(self, article: Article) -> None:
        """Queue notification tasks for users subscribed to the article's categories."""
        if not article.categories:
            return

        category_ids = [cat.id for cat in article.categories]

        result = await self.session.execute(
            select(Subscription).where(
                Subscription.category_id.in_(category_ids)
            )
        )
        subscriptions = result.scalars().all()

        if not subscriptions:
            return

        # Deduplicate by user — a user subscribed to multiple matching
        # categories should receive only one notification.
        notified_users: set[str] = set()

        for sub in subscriptions:
            if sub.teams_user_id in notified_users:
                continue
            notified_users.add(sub.teams_user_id)

            try:
                from celery import current_app as celery_app

                celery_app.send_task(
                    "src.tasks.notify.send_subscriber_notification",
                    kwargs={
                        "article_id": article.id,
                        "teams_user_id": sub.teams_user_id,
                    },
                    queue="notifications",
                )
            except Exception:
                logger.debug(
                    "Failed to queue subscriber notification for user=%s article=%s",
                    sub.teams_user_id,
                    article.id,
                    exc_info=True,
                )

        logger.info(
            "Queued notifications for %d subscribers for article id=%s",
            len(notified_users),
            article.id,
        )
