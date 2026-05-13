"""Diagnostics API — visibility into the image-extraction pipeline.

Endpoints
---------
GET  /admin/api/diagnostics/images
    Image-coverage stats + the most recent 20 articles with their image_url.

GET  /admin/api/diagnostics/images/test?url=<page_url>
    Live test: fetch the page and return the resolved image URL.

POST /admin/api/diagnostics/images/refetch/{article_id}
    Re-fetch the og:image for a single article and persist if found.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.base import get_session
from src.bot.services.teams_webhook import send_article_notification, send_test_card
from src.scrapers.article_fetcher import fetch_article, resolve_google_news_url
from src.scrapers.image_extractor import fetch_article_image

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/api/diagnostics", tags=["dashboard-diagnostics"]
)


@router.get("/images")
async def image_coverage(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=200),
):
    """Image-coverage stats and a sample of recent articles."""
    total = (await session.execute(select(func.count(Article.id)))).scalar() or 0
    with_image = (
        await session.execute(
            select(func.count(Article.id)).where(Article.image_url.isnot(None))
        )
    ).scalar() or 0

    recent = (
        await session.execute(
            select(Article).order_by(desc(Article.created_at)).limit(limit)
        )
    ).scalars().all()

    return {
        "stats": {
            "total": total,
            "with_image": with_image,
            "without_image": total - with_image,
            "coverage_pct": round(with_image / total * 100, 1) if total else 0.0,
        },
        "recent": [
            {
                "id": a.id,
                "title": a.title[:80],
                "url": a.url,
                "image_url": a.image_url,
                "has_image": a.image_url is not None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent
        ],
    }


@router.get("/images/test")
async def test_extraction(url: str = Query(..., description="Page URL to probe")):
    """Live extraction test — fetch the page and return what we find."""
    logger.info("Diagnostic image extraction test: %s", url)
    image_url = await fetch_article_image(url)
    return {"input_url": url, "image_url": image_url, "found": image_url is not None}


@router.get("/resolve")
async def diag_resolve(url: str = Query(..., description="Google News URL to resolve")):
    """Resolve a Google News URL to its underlying publisher URL."""
    logger.info("Diagnostic resolve: %s", url)
    resolved = await resolve_google_news_url(url)
    return {"input_url": url, "resolved_url": resolved, "resolved": resolved is not None}


@router.get("/fetch")
async def diag_fetch(url: str = Query(..., description="Article URL")):
    """Full end-to-end fetch: resolve + scrape + extract everything."""
    logger.info("Diagnostic full fetch: %s", url)
    payload = await fetch_article(url)
    body = payload.get("body_markdown") or ""
    return {
        "input_url": url,
        "resolved_url": payload.get("resolved_url"),
        "title": payload.get("title"),
        "description": payload.get("description"),
        "author": payload.get("author"),
        "published_at": (
            payload.get("published_at").isoformat()
            if payload.get("published_at") else None
        ),
        "image_url": payload.get("image_url"),
        "body_length": len(body),
        "body_preview": body[:500],
    }


@router.post("/images/refetch/{article_id}")
async def refetch_image(
    article_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Re-fetch the og:image for one article and persist the result."""
    article = (
        await session.execute(select(Article).where(Article.id == article_id))
    ).scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    previous = article.image_url
    image_url = await fetch_article_image(article.url)
    if image_url:
        article.image_url = image_url
        await session.commit()

    return {
        "article_id": article_id,
        "url": article.url,
        "previous_image_url": previous,
        "new_image_url": image_url,
        "updated": bool(image_url and image_url != previous),
    }


@router.get("/content")
async def content_coverage(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=200),
):
    """Stats on article body length + URL vs. content ratio."""
    total = (await session.execute(select(func.count(Article.id)))).scalar() or 0
    if total == 0:
        return {"stats": {"total": 0}, "recent": []}

    recent = (
        await session.execute(
            select(Article).order_by(desc(Article.created_at)).limit(limit)
        )
    ).scalars().all()

    short = sum(1 for a in recent if (a.markdown_content or "").strip().__len__() < 400)
    empty = sum(1 for a in recent if not (a.markdown_content or "").strip())

    return {
        "stats": {
            "total": total,
            "sample_size": len(recent),
            "sample_empty_content": empty,
            "sample_short_content": short,
        },
        "recent": [
            {
                "id": a.id,
                "title": a.title[:80],
                "url": a.url,
                "content_length": len((a.markdown_content or "").strip()),
                "has_image": a.image_url is not None,
                "is_google_news": "news.google.com" in (a.url or ""),
            }
            for a in recent
        ],
    }


@router.post("/teams/test")
async def teams_test() -> dict:
    """Send a one-off setup-test card to the Teams webhook."""
    ok, msg = await send_test_card()
    return {"ok": ok, "message": msg}


@router.post("/teams/notify/{article_id}")
async def teams_notify(
    article_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Send the given article as a notification to the Teams webhook,
    bypassing the source allowlist / score threshold. For testing."""
    from sqlalchemy.orm import selectinload
    from src.models.article import Article

    article = (
        await session.execute(
            select(Article)
            .options(selectinload(Article.source), selectinload(Article.summaries))
            .where(Article.id == article_id)
        )
    ).scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    ok = await send_article_notification(article)
    return {"article_id": article_id, "sent": ok}


@router.post("/content/refetch/{article_id}")
async def refetch_content(
    article_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Re-fetch the full article page and persist title/body/image/author."""
    article = (
        await session.execute(select(Article).where(Article.id == article_id))
    ).scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    payload = await fetch_article(article.url)
    if not payload:
        return {"article_id": article_id, "updated": False, "reason": "fetch_failed"}

    before = {
        "url": article.url,
        "title": article.title,
        "content_length": len(article.markdown_content or ""),
        "image_url": article.image_url,
    }

    resolved = payload.get("resolved_url")
    if resolved and resolved != article.url:
        article.url = resolved

    body = payload.get("body_markdown") or ""
    if len(body) > len(article.markdown_content or ""):
        article.markdown_content = body

    if payload.get("title") and (
        not article.title or article.title == "Untitled"
    ):
        article.title = payload["title"].strip()[:1024]

    if payload.get("author") and not article.author:
        article.author = payload["author"][:512]

    if payload.get("image_url") and not article.image_url:
        article.image_url = payload["image_url"]

    await session.commit()

    return {
        "article_id": article_id,
        "updated": True,
        "before": before,
        "after": {
            "url": article.url,
            "title": article.title,
            "content_length": len(article.markdown_content or ""),
            "image_url": article.image_url,
        },
        "resolved_url": resolved,
    }
