"""Articles API endpoints for the admin dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.base import get_session
from src.models.article import Article
from src.models.article_category import ArticleCategory
from src.models.category import Category
from src.models.source import Source
from src.models.summary import Summary

router = APIRouter(prefix="/admin/api/articles", tags=["dashboard-articles"])


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _article_to_dict(article: Article, *, detail: bool = False) -> dict:
    data = {
        "id": article.id,
        "title": article.title,
        "url": article.url,
        "source_name": article.source.name if article.source else "N/A",
        "source_id": article.source_id,
        "categories": [c.name for c in article.categories] if article.categories else [],
        "importance_score": article.importance_score,
        "is_breaking": article.is_breaking,
        "pipeline_status": article.pipeline_status,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "created_at": article.created_at.isoformat() if article.created_at else None,
    }
    if detail:
        data["author"] = article.author
        data["markdown_content"] = article.markdown_content
        data["summaries"] = [
            {
                "id": s.id,
                "headline": s.headline,
                "summary_text": s.summary_text,
                "llm_provider": s.llm_provider,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in (article.summaries or [])
        ]
    return data


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("")
async def list_articles(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    score_min: Optional[int] = None,
    score_max: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """Return a paginated, filtered list of articles."""

    query = select(Article).options(
        selectinload(Article.source),
        selectinload(Article.categories),
    )

    # --- filters ---
    if source_id is not None:
        query = query.where(Article.source_id == source_id)

    if score_min is not None:
        query = query.where(Article.importance_score >= score_min)

    if score_max is not None:
        query = query.where(Article.importance_score <= score_max)

    if status:
        query = query.where(Article.pipeline_status == status)

    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            query = query.where(Article.created_at >= dt)
        except ValueError:
            pass

    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            query = query.where(Article.created_at <= dt)
        except ValueError:
            pass

    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Article.title.ilike(pattern),
                Article.url.ilike(pattern),
            )
        )

    if category:
        query = query.join(ArticleCategory, Article.id == ArticleCategory.article_id).join(
            Category, Category.id == ArticleCategory.category_id
        ).where(Category.name == category)

    # --- count ---
    count_q = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_q)
    total: int = total_result.scalar() or 0

    # --- pagination ---
    query = query.order_by(Article.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await session.execute(query)
    articles = result.scalars().unique().all()

    return {
        "items": [_article_to_dict(a) for a in articles],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
    }


@router.get("/{article_id}")
async def get_article(article_id: int, session: AsyncSession = Depends(get_session)):
    """Return a single article with its summary and categories."""
    result = await session.execute(
        select(Article)
        .options(
            selectinload(Article.source),
            selectinload(Article.categories),
            selectinload(Article.summaries),
        )
        .where(Article.id == article_id)
    )
    article = result.scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return _article_to_dict(article, detail=True)
