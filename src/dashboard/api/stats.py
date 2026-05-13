"""Stats API endpoints for the admin dashboard."""

from __future__ import annotations

from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import get_session
from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.models.post_log import PostLog
from src.models.source import Source

router = APIRouter(prefix="/admin/api/stats", tags=["dashboard-stats"])


def _today_start() -> datetime:
    """Return midnight UTC of the current day (naive, matching DB column)."""
    return datetime.combine(datetime.now(timezone.utc).date(), time.min)


@router.get("")
async def get_stats(session: AsyncSession = Depends(get_session)):
    """Return aggregate statistics used by the overview dashboard."""

    today = _today_start()

    # Total articles today
    total_today_q = await session.execute(
        select(func.count(Article.id)).where(Article.created_at >= today)
    )
    total_articles_today: int = total_today_q.scalar() or 0

    # Scrape success rate (sources with error_count == 0 / total enabled)
    src_q = await session.execute(
        select(
            func.count(Source.id).label("total"),
            func.sum(case((Source.error_count == 0, 1), else_=0)).label("ok"),
        ).where(Source.enabled == True)  # noqa: E712
    )
    row = src_q.one()
    total_sources = row.total or 0
    ok_sources = row.ok or 0
    scrape_success_rate = round((ok_sources / total_sources * 100) if total_sources else 100, 1)

    # Active sources (enabled)
    active_sources = total_sources

    # Total sources (including disabled)
    all_sources_q = await session.execute(select(func.count(Source.id)))
    all_sources: int = all_sources_q.scalar() or 0

    # Breaking alerts today
    breaking_q = await session.execute(
        select(func.count(PostLog.id)).where(
            PostLog.post_type == "alert",
            PostLog.posted_at >= today,
        )
    )
    breaking_today: int = breaking_q.scalar() or 0

    # Articles by category
    cat_q = await session.execute(
        select(Category.name, func.count(ArticleCategory.article_id))
        .join(ArticleCategory, Category.id == ArticleCategory.category_id)
        .join(Article, Article.id == ArticleCategory.article_id)
        .where(Article.created_at >= today)
        .group_by(Category.name)
        .order_by(func.count(ArticleCategory.article_id).desc())
    )
    articles_by_category = {name: count for name, count in cat_q.all()}

    # Recent articles (last 5)
    recent_q = await session.execute(
        select(Article.id, Article.title, Article.created_at)
        .order_by(Article.created_at.desc())
        .limit(5)
    )
    recent_articles = [
        {"id": row.id, "title": row.title, "created_at": row.created_at.isoformat() if row.created_at else None}
        for row in recent_q.all()
    ]

    return {
        "total_articles_today": total_articles_today,
        "scrape_success_rate": scrape_success_rate,
        "total_sources": all_sources,
        "active_sources": active_sources,
        "breaking_alerts_today": breaking_today,
        "articles_by_category": articles_by_category,
        "recent_articles": recent_articles,
    }
