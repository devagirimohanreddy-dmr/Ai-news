"""Teams notification feed — RSS endpoint consumed by a Power Automate flow.

Microsoft Teams notifications work via a "pull" flow:

    [Our server] ──► RSS feed ◄── [Power Automate]
                                        │
                                        └── posts adaptive card ──► Teams channel

The Power Automate flow uses its standard *"When a feed item is published"*
trigger pointed at this endpoint. No OAuth, no Azure AD App Registration,
no incoming-webhook connector — just a URL Power Automate polls every
~15 minutes.

The feed is filtered to:
    * Sources marked ``notify_to_teams = True``
    * Articles with ``importance_score >= TEAMS_NOTIFICATION_MIN_SCORE``
        (breaking news bypasses the threshold)
    * Last 7 days only — anything older has already been seen
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config.settings import settings
from src.models.article import Article
from src.models.base import get_session
from src.models.source import Source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/teams", tags=["teams-feed"])

# Shared-secret token for the RSS URL. Power Automate puts this in the query
# string of the URL we hand it. Anyone with the URL can read the feed —
# treat the token like a password.
_FEED_TOKEN = os.environ.get("TEAMS_FEED_TOKEN", "change-me-please-set-TEAMS_FEED_TOKEN")
_FEED_MAX_ITEMS = 50
_FEED_MAX_AGE_DAYS = 7


_JSON_HOSTILE_CHARS = re.compile(r'[\\\"\x00-\x1f\x7f]')


def _json_safe(text: str | None) -> str:
    """Strip every character that can break JSON when substituted into a
    Power Automate ``@{...}`` placeholder inside a JSON string field.

    Power Automate doesn't escape the substituted value when building the
    Adaptive Card body, so any ``"``, ``\\``, newline, or control character
    in the resolved text breaks the whole card with
    ``InvalidBotRequestMessageBody``. We replace those characters here so
    the downstream JSON stays parseable.
    """
    if not text:
        return ""
    # Smart quotes / curly quotes -> ASCII apostrophe (always JSON-safe).
    smart_map = {
        "“": "'", "”": "'", "„": "'", "‟": "'",
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "«": "'", "»": "'",
        "…": "...",
    }
    for smart, plain in smart_map.items():
        text = text.replace(smart, plain)
    # Replace any remaining double-quotes and backslashes (the two characters
    # that actually break JSON if unescaped).
    text = text.replace("\\", " ").replace('"', "'")
    # Collapse newlines / tabs / control chars into a single space.
    text = _JSON_HOSTILE_CHARS.sub(" ", text)
    # Collapse multiple spaces.
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _esc(text: str | None) -> str:
    """Escape text for safe inclusion as XML PCDATA AND JSON substitution."""
    if not text:
        return ""
    # JSON-safe first, then HTML-escape for XML.
    return html.escape(_json_safe(text), quote=False)


def _cdata(text: str | None) -> str:
    """Wrap JSON-sanitized text in CDATA for XML transport."""
    if not text:
        return "<![CDATA[]]>"
    safe = _json_safe(text).replace("]]>", "]]&gt;")
    return f"<![CDATA[{safe}]]>"


def _short_preview(article: Article, max_chars: int = 240) -> str:
    """Two-line plain-text preview pulled from the AI summary."""
    summary_text = ""
    if article.summaries:
        summary_text = (article.summaries[-1].summary_text or "").strip()
    if not summary_text:
        # Fallback to the article body's first paragraph.
        body = (article.markdown_content or "").strip()
        # First non-heading paragraph
        for line in body.split("\n\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                summary_text = line
                break
    if len(summary_text) > max_chars:
        summary_text = summary_text[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return summary_text


@router.get("/feed.rss")
async def teams_rss_feed(
    token: str = Query(..., description="Shared secret to authorize the consumer"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Standard RSS 2.0 feed of articles eligible for Teams notification."""
    if token != _FEED_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    cutoff = datetime.now(timezone.utc) - timedelta(days=_FEED_MAX_AGE_DAYS)
    cutoff_naive = cutoff.replace(tzinfo=None)
    min_score = settings.TEAMS_NOTIFICATION_MIN_SCORE

    stmt = (
        select(Article)
        .join(Source, Article.source_id == Source.id)
        .options(
            selectinload(Article.source),
            selectinload(Article.summaries),
        )
        .where(Source.notify_to_teams == True)  # noqa: E712
        .where(
            or_(
                Article.is_breaking == True,  # noqa: E712
                Article.importance_score >= min_score,
            )
        )
        .where(Article.created_at >= cutoff_naive)
        .order_by(desc(Article.created_at))
        .limit(_FEED_MAX_ITEMS)
    )
    result = await session.execute(stmt)
    articles = result.scalars().all()

    items_xml: list[str] = []
    for a in articles:
        pub_dt = a.published_at or a.created_at
        if pub_dt and pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        pub_str = format_datetime(pub_dt) if pub_dt else ""

        source_name = a.source.name if a.source else "AI News"
        headline = (
            (a.summaries[-1].headline if (a.summaries and a.summaries[-1].headline) else a.title)
            or "Untitled"
        )
        preview = _short_preview(a)

        # Embed image as <enclosure>. Power Automate's RSS trigger exposes
        # this so the flow can put it into the card body.
        enclosure = ""
        if a.image_url:
            enclosure = (
                f'<enclosure url="{_esc(a.image_url)}" '
                f'length="0" type="image/jpeg" />'
            )

        # description includes the source name + preview so Power Automate
        # users can pull either via the standard `description` field.
        description = preview

        item = (
            "  <item>\n"
            f"    <title>{_esc(headline)}</title>\n"
            f"    <link>{_esc(a.url)}</link>\n"
            f"    <description>{_cdata(description)}</description>\n"
            f"    <category>{_esc(source_name)}</category>\n"
            f"    <author>noreply@ainews.local ({_esc(source_name)})</author>\n"
            f"    <pubDate>{pub_str}</pubDate>\n"
            f"    <guid isPermaLink=\"false\">ainews-{a.id}</guid>\n"
            f"    {enclosure}\n"
            "  </item>"
        )
        items_xml.append(item)

    now_str = format_datetime(datetime.now(timezone.utc))
    feed_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        "    <title>AI News — Teams Notifications</title>\n"
        "    <link>https://ainews.local/</link>\n"
        "    <description>"
        f"Articles from allowlisted sources, importance ≥ {min_score}."
        "</description>\n"
        "    <language>en-us</language>\n"
        f"    <lastBuildDate>{now_str}</lastBuildDate>\n"
        + "\n".join(items_xml)
        + "\n  </channel>\n</rss>\n"
    )

    logger.info(
        "Served Teams RSS feed: %d items, threshold=%d",
        len(items_xml), min_score,
    )
    return Response(content=feed_xml, media_type="application/rss+xml")
