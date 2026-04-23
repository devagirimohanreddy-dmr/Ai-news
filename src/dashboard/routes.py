"""FastAPI router serving the admin dashboard HTML pages."""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.models.base import get_session
from src.models.article import Article

_BASE_DIR = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

router = APIRouter(prefix="/admin")


@router.get("/")
async def overview(request: Request):
    """Dashboard home / overview page."""
    return templates.TemplateResponse(request=request, name="overview.html")


@router.get("/sources")
async def sources_page(request: Request):
    """Sources management page."""
    return templates.TemplateResponse(request=request, name="sources.html")


@router.get("/articles")
async def articles_page(request: Request):
    """Articles browser page."""
    return templates.TemplateResponse(request=request, name="articles.html")


@router.get("/categories")
async def categories_page(request: Request):
    """Categories management page."""
    return templates.TemplateResponse(request=request, name="categories.html")


@router.get("/logs")
async def logs_page(request: Request):
    """Post-logs viewer page."""
    return templates.TemplateResponse(request=request, name="logs.html")


@router.get("/commands")
async def commands_page(request: Request):
    """Command tester page."""
    return templates.TemplateResponse(request=request, name="commands.html")


@router.get("/articles/{article_id}")
async def article_detail_page(
    request: Request,
    article_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Article detail / reader page."""
    result = await session.execute(
        select(Article)
        .options(
            selectinload(Article.source),
            selectinload(Article.categories),
            selectinload(Article.summaries),
        )
        .where(Article.id == article_id)
    )
    article_obj = result.scalar_one_or_none()

    if article_obj is None:
        # Render a simple not-found within the layout
        return templates.TemplateResponse(
            request=request,
            name="article_detail.html",
            context={
                "article": {
                    "title": "Article not found",
                    "url": "",
                    "source_name": "",
                    "author": None,
                    "published_at": None,
                    "created_at": None,
                    "importance_score": 0,
                    "is_breaking": False,
                    "pipeline_status": "unknown",
                    "categories": [],
                    "summaries": [],
                    "markdown_content": None,
                },
            },
        )

    # Convert ORM object to a template-friendly dict
    categories = [c.name for c in article_obj.categories] if article_obj.categories else []
    summaries = []
    for s in (article_obj.summaries or []):
        summaries.append({
            "headline": s.headline,
            "summary_text": s.summary_text,
            "llm_provider": s.llm_provider,
        })

    article_data = {
        "id": article_obj.id,
        "title": article_obj.title,
        "url": article_obj.url,
        "source_name": article_obj.source.name if article_obj.source else "N/A",
        "author": article_obj.author,
        "published_at": (
            article_obj.published_at.strftime("%B %d, %Y at %H:%M UTC")
            if article_obj.published_at else None
        ),
        "created_at": (
            article_obj.created_at.strftime("%B %d, %Y at %H:%M UTC")
            if article_obj.created_at else None
        ),
        "importance_score": article_obj.importance_score,
        "is_breaking": article_obj.is_breaking,
        "pipeline_status": article_obj.pipeline_status,
        "categories": categories,
        "summaries": summaries,
        "markdown_content": article_obj.markdown_content,
        "image_url": article_obj.image_url,
    }

    return templates.TemplateResponse(
        request=request,
        name="article_detail.html",
        context={"article": article_data},
    )


@router.get("/settings")
async def settings_page(request: Request):
    """Settings page."""
    return templates.TemplateResponse(request=request, name="settings.html")
