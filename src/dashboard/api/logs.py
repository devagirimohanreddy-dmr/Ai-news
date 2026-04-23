"""Logs API endpoints for the admin dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.base import get_session
from src.models.article import Article
from src.models.post_log import PostLog

router = APIRouter(prefix="/admin/api/logs", tags=["dashboard-logs"])


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _log_to_dict(log: PostLog, article: Article | None = None) -> dict:
    return {
        "id": log.id,
        "article_id": log.article_id,
        "article_title": article.title if article else "N/A",
        "post_type": log.post_type,
        "teams_channel": log.teams_channel,
        "status": log.status,
        "posted_at": log.posted_at.isoformat() if log.posted_at else None,
    }


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("")
async def list_logs(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    post_type: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Return paginated post logs with optional filters."""

    query = (
        select(PostLog, Article)
        .outerjoin(Article, PostLog.article_id == Article.id)
    )

    if post_type:
        query = query.where(PostLog.post_type == post_type)

    if status:
        query = query.where(PostLog.status == status)

    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            query = query.where(PostLog.posted_at >= dt)
        except ValueError:
            pass

    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            query = query.where(PostLog.posted_at <= dt)
        except ValueError:
            pass

    # count
    count_q = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_q)
    total: int = total_result.scalar() or 0

    # paginate
    query = query.order_by(PostLog.posted_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await session.execute(query)
    rows = result.all()

    return {
        "items": [_log_to_dict(log, article) for log, article in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
    }
