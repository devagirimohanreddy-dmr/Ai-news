"""FastAPI router serving the admin dashboard HTML pages."""

from __future__ import annotations

import logging
import pathlib
import re

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.models.base import get_session
from src.models.article import Article

logger = logging.getLogger(__name__)

# ── Markdown -> HTML renderer ─────────────────────────────────────────
# We render server-side so the article body is always present in the
# response HTML, regardless of browser/CDN/JS state. ``markdown`` is in
# pyproject; if it's missing at runtime we degrade to a <pre> block.
try:
    import markdown as _markdown_lib

    # NOTE: do NOT enable `nl2br`. Markdown content stored in the DB was
    # produced by markdownify which preserves source line wrapping from the
    # original HTML. With `nl2br`, every preserved newline becomes a hard
    # <br>, so paragraphs render as a column of short lines down the left
    # edge of the container instead of flowing edge-to-edge.
    _MD = _markdown_lib.Markdown(
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )

    def _render_markdown(text: str) -> str:
        if not text:
            return ""
        try:
            _MD.reset()
            return _MD.convert(text)
        except Exception:
            logger.exception("Markdown render failed; falling back to <pre>")
            import html as _html
            return "<pre style='white-space:pre-wrap;'>" + _html.escape(text) + "</pre>"

except Exception:  # pragma: no cover — markdown lib not installed at all
    _MD = None
    logger.warning("Python `markdown` not available; falling back to <pre>.")

    def _render_markdown(text: str) -> str:
        import html as _html
        return "<pre style='white-space:pre-wrap;'>" + _html.escape(text or "") + "</pre>"


# ── Summary sanitizer ─────────────────────────────────────────────────
# Existing summary rows were generated before the summarizer started
# stripping HTML tags / URLs. Clean them at render time too so we never
# show raw <a href="news.google.com/...">… in the UI.
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
_WS_RE = re.compile(r"\s+")


def _sanitize_summary(text: str | None) -> str:
    if not text:
        return ""
    s = _MD_LINK_RE.sub(r"\1", text)
    s = _TAG_RE.sub(" ", s)
    s = _URL_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s

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
                    "id": article_id,
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
                    "content_html": "",
                    "content_length": 0,
                    "image_url": None,
                },
            },
        )

    # Convert ORM object to a template-friendly dict
    categories = [c.name for c in article_obj.categories] if article_obj.categories else []
    summaries = []
    for s in (article_obj.summaries or []):
        summaries.append({
            "headline": _sanitize_summary(s.headline) or s.headline,
            "summary_text": _sanitize_summary(s.summary_text),
            "llm_provider": s.llm_provider,
        })

    raw_md = (article_obj.markdown_content or "").strip()
    content_html = _render_markdown(raw_md) if raw_md else ""

    logger.info(
        "Article detail rendered id=%s body_len=%d html_len=%d image=%s",
        article_obj.id,
        len(raw_md),
        len(content_html),
        bool(article_obj.image_url),
    )

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
        "markdown_content": raw_md,
        "content_html": content_html,
        "content_length": len(raw_md),
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
