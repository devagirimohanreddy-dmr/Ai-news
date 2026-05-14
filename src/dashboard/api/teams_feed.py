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


_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]?(?=\s|$)")


def _truncate_clean(text: str, target_chars: int) -> str:
    """Cut ``text`` to at most ``target_chars`` ending cleanly.

    Prefers the last complete sentence boundary (``.``, ``!``, ``?``)
    within the window. If none exists, falls back to the last full word
    boundary. **Never appends ``...``** — the result is always either a
    sentence that ended naturally or a partial fragment that the reader
    can understand from context.
    """
    text = text.strip()
    if len(text) <= target_chars:
        return text

    window = text[:target_chars]
    matches = list(_SENTENCE_END_RE.finditer(window))
    if matches:
        # Cut just after the last sentence-ending punctuation.
        end = matches[-1].end()
        return window[:end].rstrip()

    # No sentence boundary found — fall back to last word boundary.
    if " " in window:
        return window.rsplit(" ", 1)[0].rstrip()
    return window.rstrip()


def _short_preview(article: Article, target_chars: int = 420) -> str:
    """Plain-text preview (~5-7 lines worth) pulled from the AI summary.

    Uses the AI-generated summary first; supplements with body
    paragraphs if too short. Cleanly truncated at a sentence boundary
    (preferred) or word boundary — **never with a trailing ``...``**.
    """
    summary_text = ""
    if article.summaries:
        summary_text = (article.summaries[-1].summary_text or "").strip()
    if not summary_text or len(summary_text) < 200:
        # AI summary missing or too short — supplement with body paragraphs.
        body = (article.markdown_content or "").strip()
        extras: list[str] = []
        for line in body.split("\n\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                extras.append(line)
            if sum(len(e) for e in extras) > target_chars * 2:
                break
        joined = " ".join(extras)
        if summary_text and joined:
            summary_text = summary_text + " " + joined
        elif joined:
            summary_text = joined

    return _truncate_clean(summary_text, target_chars)


def _shorten_topic(name: str) -> str:
    """Trim long LLM topic labels like 'AI Engineering & Developer Tools'
    down to the leading phrase ('AI Engineering')."""
    for sep in (" & ", ", ", " - ", ": "):
        if sep in name:
            return name.split(sep)[0].strip()
    return name.strip()


_GEN_AI_KW = (
    "gpt", "llm", "chatgpt", "claude", "gemini", "openai", "anthropic",
    "generative", "image generation", "text generation", "copilot",
    "stable diffusion", "midjourney", "sora", "dall-e", "dalle",
)
_REPO_KW = (
    "github", "open source", "open-source", "repo", "repository",
    "pull request", "commit", "merge", "fork", "release", "library",
    "framework", "sdk",
)
_SECURITY_KW = (
    "security", "vulnerability", "cve", "exploit", "hack", "breach",
    "ransomware", "phishing", "0-day", "zero-day", "malware",
)
_RESEARCH_KW = (
    "research", "paper", "arxiv", "study", "benchmark", "preprint",
)
_AI_KW = (
    "ai ", " ai", "artificial intelligence", "machine learning", "ml ",
    " ml", "neural", "deep learning", "model", "transformer",
    "fine-tun", "training", "inference",
)


def _topic_category(article: Article) -> str:
    """Pick a short topic tag for the article's heading.

    Never returns the source name. Order of preference:
        1. Shortened LLM-assigned category (e.g. ``Generative AI``,
           ``AI Engineering``).
        2. Title-keyword heuristic mapping to one of a small set of tags
           (``Gen AI``, ``AI``, ``Repo``, ``Security``, ``Research``,
           ``Tech``).
    """
    # 1. LLM-assigned category — always preferred when present
    if article.categories:
        for cat in article.categories:
            name = (cat.name or "").strip()
            if not name:
                continue
            # Skip categories that are clearly source names (defensive).
            if article.source and name == article.source.name:
                continue
            return _shorten_topic(name)

    # 2. Title-keyword heuristic
    title = (article.title or "").lower()

    def has_any(words: tuple[str, ...]) -> bool:
        return any(w in title for w in words)

    if has_any(_GEN_AI_KW):
        return "Gen AI"
    if has_any(_REPO_KW):
        return "Repo"
    if has_any(_SECURITY_KW):
        return "Security"
    if has_any(_RESEARCH_KW):
        return "Research"
    if has_any(_AI_KW):
        return "AI"
    return "Tech"


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
            selectinload(Article.categories),
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
        # Use the LATER of created_at and published_at as the RSS pubDate.
        # This ensures freshly-ingested articles always look "new" to the
        # Power Automate RSS trigger — older publisher timestamps don't
        # cause PA to skip articles we just discovered.
        candidates = [t for t in (a.created_at, a.published_at) if t]
        pub_dt = max(candidates) if candidates else None
        if pub_dt and pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        pub_str = format_datetime(pub_dt) if pub_dt else ""

        source_name = a.source.name if a.source else "AI News"
        topic = _topic_category(a)  # used as the card heading
        headline = (
            (a.summaries[-1].headline if (a.summaries and a.summaries[-1].headline) else a.title)
            or "Untitled"
        )
        preview = _short_preview(a)

        # We expose the image URL in TWO places so the Power Automate
        # Adaptive Card has reliable access regardless of how the RSS
        # connector parses fields:
        #   1. Standard <enclosure> tag (some PA versions expose this).
        #   2. As the second <link> with rel="enclosure" (Atom-style).
        # Image URL — real one if available, otherwise a stable placeholder.
        image_for_card = a.image_url or (
            "https://via.placeholder.com/1200x630/0078D4/ffffff.png?text=AI+News"
        )

        enclosure = ""
        if a.image_url:
            enclosure = (
                f'<enclosure url="{_esc(a.image_url)}" '
                f'length="0" type="image/jpeg" />'
            )

        # Power Automate's RSS connector reliably exposes `summary` (the
        # description) as a plain string. We embed the image URL and the
        # body separated by ``|||`` — a delimiter that never appears in
        # real article text or URLs.
        #     Adaptive Card:
        #       Image: @{first(split(summary, '|||'))}
        #       Body:  @{last(split(summary, '|||'))}
        description = f"{image_for_card}|||{preview}"
        item = (
            "  <item>\n"
            f"    <title>{_esc(headline)}</title>\n"
            f"    <link>{_esc(a.url)}</link>\n"
            f"    <description>{_cdata(description)}</description>\n"
            f"    <category>{_esc(topic)}</category>\n"
            f"    <category>{_esc(source_name)}</category>\n"
            f"    <category>{_esc(image_for_card)}</category>\n"
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
