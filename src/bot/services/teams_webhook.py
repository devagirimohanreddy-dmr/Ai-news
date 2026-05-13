"""Teams channel-notification service.

Posts an Adaptive Card to a Power Automate / Workflows incoming webhook
whenever an allowlisted article passes its importance threshold.

Supports two webhook auth modes:

1. **Anonymous** — legacy ``logic.azure.com`` "When a HTTP request is
   received" trigger. URL itself contains the signature. No auth header.

2. **OAuth client-credentials** — newer ``powerplatform.com`` "Direct API"
   trigger. Requires an Azure AD app registration; the three
   ``AZURE_AD_*`` settings hold the credentials. We cache the access token
   in memory and refresh on 401.

Card format: kicker (source) + headline + two-line description + Read More
button — exactly the spec the user requested.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.config.settings import settings
from src.models.article import Article

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Card builder                                                                 #
# --------------------------------------------------------------------------- #

# Strip markdown / HTML to plain text for the 2-line preview.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s*")


def _short_summary(article: Article, max_chars: int = 220) -> str:
    """Two-line plain-text preview for the card.

    Prefers the AI summary; falls back to the start of the body markdown.
    All HTML / markdown / URLs stripped so the card renders cleanly.
    """
    text = ""
    if article.summaries:
        # Most recent summary
        text = article.summaries[-1].summary_text or ""
    if not text:
        text = article.markdown_content or ""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _TAG_RE.sub(" ", text)
    text = _URL_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return text


def _build_card(article: Article) -> dict:
    """Build the Adaptive Card payload Power Automate expects.

    The outer envelope is what the "Post adaptive card" action consumes.
    """
    source_name = (article.source.name if article.source else "AI News")
    headline = (
        article.summaries[-1].headline
        if article.summaries and article.summaries[-1].headline
        else article.title
    )
    headline = (headline or "Untitled")[:200]
    preview = _short_summary(article)

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": source_name.upper(),
            "weight": "Bolder",
            "size": "Small",
            "color": "Accent",
            "isSubtle": True,
            "spacing": "None",
        },
        {
            "type": "TextBlock",
            "text": headline,
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
            "spacing": "Small",
        },
    ]
    if preview:
        body.append(
            {
                "type": "TextBlock",
                "text": preview,
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
                "maxLines": 2,
            }
        )

    if article.image_url:
        body.insert(
            2,
            {
                "type": "Image",
                "url": article.image_url,
                "size": "Stretch",
                "spacing": "Medium",
                "altText": headline,
            },
        )

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "Read more",
                "url": article.url,
            }
        ],
    }

    if article.is_breaking:
        body.insert(
            0,
            {
                "type": "TextBlock",
                "text": "⚡ BREAKING",
                "weight": "Bolder",
                "size": "Small",
                "color": "Attention",
                "spacing": "None",
            },
        )

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Token cache for OAuth-protected webhooks                                    #
# --------------------------------------------------------------------------- #


class _TokenCache:
    """Simple in-process token cache for the client-credentials flow."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: datetime = datetime.now(timezone.utc)

    def get(self) -> str | None:
        if self._token and self._expires_at > datetime.now(timezone.utc) + timedelta(seconds=30):
            return self._token
        return None

    def set(self, token: str, expires_in_seconds: int) -> None:
        self._token = token
        self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)


_token_cache = _TokenCache()


async def _fetch_access_token() -> str | None:
    """Get an Azure AD access token via the client-credentials flow.

    Returns ``None`` if any of the three OAuth credentials is missing —
    the caller will then attempt the webhook anonymously.
    """
    tenant = settings.AZURE_AD_TENANT_ID
    client_id = settings.AZURE_AD_CLIENT_ID
    client_secret = settings.AZURE_AD_CLIENT_SECRET
    if not (tenant and client_id and client_secret):
        return None
    cached = _token_cache.get()
    if cached:
        return cached

    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    # Default scope for Power Automate Direct API workflows
    scope = "https://service.flow.microsoft.com//.default"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": scope,
                },
            )
        if r.status_code != 200:
            logger.warning(
                "Azure AD token fetch failed: HTTP %s %s", r.status_code, r.text[:200]
            )
            return None
        data = r.json()
        token = data.get("access_token")
        if token:
            _token_cache.set(token, int(data.get("expires_in", 3600)))
            return token
    except Exception as exc:
        logger.warning("Azure AD token fetch error: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


async def send_article_notification(article: Article) -> bool:
    """POST an Adaptive Card for ``article`` to the configured Teams webhook.

    Returns ``True`` on HTTP 2xx, ``False`` otherwise. Never raises.
    """
    if not settings.TEAMS_NOTIFICATIONS_ENABLED:
        return False
    if not settings.TEAMS_WEBHOOK_URL:
        return False

    payload = _build_card(article)

    headers = {"Content-Type": "application/json"}
    token = await _fetch_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(settings.TEAMS_WEBHOOK_URL, json=payload, headers=headers)
    except Exception as exc:
        logger.warning(
            "Teams webhook POST failed for article id=%s: %s", article.id, exc
        )
        return False

    if 200 <= r.status_code < 300:
        logger.info(
            "Teams notification sent: article id=%s title=%r",
            article.id, (article.title or "")[:60],
        )
        return True

    # If we got 401 and we DID use a token, the token may be stale —
    # clear the cache so the next call refreshes.
    if r.status_code == 401:
        _token_cache.__init__()  # reset
        logger.warning(
            "Teams webhook returned 401 for article id=%s. Body: %s",
            article.id, r.text[:200],
        )
    else:
        logger.warning(
            "Teams webhook returned HTTP %s for article id=%s. Body: %s",
            r.status_code, article.id, r.text[:200],
        )
    return False


async def send_test_card() -> tuple[bool, str]:
    """Send a one-off setup-test card to confirm the webhook is wired up.

    Returns ``(ok, message)`` — ``message`` describes the outcome for
    surfacing in diagnostics / admin UI.
    """
    if not settings.TEAMS_WEBHOOK_URL:
        return False, "TEAMS_WEBHOOK_URL is not set"
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "AI NEWS BOT — SETUP TEST",
                            "weight": "Bolder",
                            "size": "Small",
                            "color": "Accent",
                            "isSubtle": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": "Hello from the AI News pipeline 👋",
                            "weight": "Bolder",
                            "size": "Large",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": "If you can see this in your Teams channel, the webhook is wired up correctly. Article notifications from your allowlisted sources will appear here automatically.",
                            "wrap": True,
                            "isSubtle": True,
                        },
                    ],
                },
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    token = await _fetch_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(settings.TEAMS_WEBHOOK_URL, json=payload, headers=headers)
    except Exception as exc:
        return False, f"POST failed: {exc}"
    if 200 <= r.status_code < 300:
        return True, "Card delivered (check your Teams channel)"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"
