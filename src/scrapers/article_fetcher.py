"""Full-article fetcher.

Given an article URL — including Google News redirect URLs — fetch the
underlying webpage, follow redirects, and extract a structured payload:

    {
        "resolved_url": str,     # final URL after redirects
        "title":        str | None,
        "description":  str | None,   # og:description / meta description
        "body_markdown": str,         # readability-extracted body, as markdown
        "author":       str | None,
        "published_at": datetime | None,
        "image_url":    str | None,
    }

The fetcher never raises — failures are logged at DEBUG and produce a
partial result with whatever fields could be resolved (or an empty dict
if the page could not be fetched at all).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx
from markdownify import markdownify as md
from readability import parse as readability_parse

from src.scrapers.image_extractor import (
    _best_content_image,
    _extract_jsonld_image,
    _extract_meta_image,
    _looks_like_site_logo,
    _validate_url,
)

logger = logging.getLogger(__name__)

# Meta-tag patterns ----------------------------------------------------------

_META_RE = re.compile(
    r'<meta\b[^>]*?\b(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*?\bcontent\s*=\s*["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_META_RE_REV = re.compile(
    r'<meta\b[^>]*?\bcontent\s*=\s*["\']([^"\']*)["\'][^>]*?\b(?:property|name)\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)

# Max HTML bytes to read for a single article page
_MAX_BYTES = 600_000

# Look like a real browser. Bot-flagged UAs get 401/403/429 from major
# publisher sites (WSJ, NYT, OpenAI, PCMag, Venturebeat, etc.).
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


# --------------------------------------------------------------------------- #
# URL handling — Google News, etc.                                            #
# --------------------------------------------------------------------------- #

def _is_google_news_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return host.endswith("news.google.com")


# Patterns we use to fish the real URL out of a Google News article page.
_GN_DATA_N_AU_RE = re.compile(r'data-n-au=["\'](https?://[^"\']+)["\']', re.IGNORECASE)
_GN_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\'>\s]+)',
    re.IGNORECASE,
)
_GN_LOCATION_RE = re.compile(
    r"""location\.(?:replace|href\s*=)\s*\(?["'](https?://[^"']+)["']""",
    re.IGNORECASE,
)
_GN_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](https?://[^"\']+)["\']',
    re.IGNORECASE,
)
_GN_ANY_HREF_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


def _decode_google_news_path(url: str) -> str | None:
    """Decode old-style Google News article IDs.

    These URLs look like
    ``https://news.google.com/rss/articles/CBMi<base64>?oc=5`` and the
    base64 path component contains a protobuf with the original URL embedded
    as a length-prefixed bytestring. We just regex for ``http(s)://...`` in
    the decoded bytes — crude but effective on the old format.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", parsed.path)
    if not m:
        return None
    encoded = m.group(1)
    encoded += "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded)
    except Exception:
        return None
    m2 = re.search(rb'https?://[^\x00-\x1f"\\<>]+', raw)
    if not m2:
        return None
    candidate = m2.group(0).decode("utf-8", errors="replace")
    # The decoded bytes can have trailing garbage after the URL — trim to the
    # last printable char of a plausible URL termination.
    candidate = re.sub(r"[\s\?\&]+\w{0,4}$", "", candidate)
    if "news.google.com" in candidate:
        return None
    return candidate


def _scrape_gnews_html_for_url(html: str) -> str | None:
    """Find the publisher URL embedded in a Google News article HTML page."""
    for rx in (_GN_DATA_N_AU_RE, _GN_META_REFRESH_RE, _GN_LOCATION_RE, _GN_CANONICAL_RE):
        m = rx.search(html)
        if m:
            cand = m.group(1).strip()
            if "news.google.com" not in cand and "gstatic" not in cand:
                return cand
    # Last-resort: first external http(s) link that isn't Google
    for m in _GN_ANY_HREF_RE.finditer(html):
        cand = m.group(1).strip()
        host = urlparse(cand).netloc.lower()
        if not host or host.endswith("google.com") or host.endswith("gstatic.com"):
            continue
        return cand
    return None


_GN_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_GN_TS_RE = re.compile(r'data-n-a-ts="(\d+)"')
_GN_AID_RE = re.compile(r'data-n-a-id="([^"]+)"')
_GN_BATCHEXEC_URL = (
    "https://news.google.com/_/DotsSplashUi/data/batchexecute"
    "?rpcids=Fbv4je&source-path=%2Frss%2Farticles&hl=en-US&gl=US"
)


async def _resolve_via_batchexecute(
    aid: str, ts: str, sig: str, client: httpx.AsyncClient
) -> str | None:
    """Call Google's internal ``Fbv4je`` RPC to translate ``aid`` to a real URL.

    The protocol: POST form-encoded ``f.req`` containing a nested envelope
    ``[[ [rpc_id, payload_json, null, "generic"] ]]``. The server responds
    with a chunked text stream prefixed by ``)]}'`` containing a JSON
    payload — the inner result is ``[ "garturlres", PUBLISHER_URL, ... ]``.
    """
    inner_payload = [
        "garturlreq",
        [
            [
                "X", "X", ["X", "X"], None, None, 1, 1, "US:en",
                None, 1, None, None, None, None, 0, 1,
            ],
            "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
        ],
        aid,
        int(ts),
        sig,
    ]
    envelope = [[["Fbv4je", json.dumps(inner_payload), None, "generic"]]]
    body = "f.req=" + quote(json.dumps(envelope), safe="")

    try:
        resp = await client.post(
            _GN_BATCHEXEC_URL,
            content=body,
            headers={
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8"
            },
        )
    except Exception as exc:
        logger.debug("batchexecute POST failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.debug("batchexecute returned HTTP %s", resp.status_code)
        return None

    text = resp.text
    if text.startswith(")]}'"):
        text = text.split("\n", 1)[-1]
    m = re.search(r"\[\[.*\]\]", text, re.DOTALL)
    if not m:
        return None
    try:
        outer = json.loads(m.group(0))
        inner_str = outer[0][2]
        if not inner_str:
            return None
        inner = json.loads(inner_str)
    except (ValueError, IndexError, TypeError) as exc:
        logger.debug("batchexecute JSON parse failed: %s", exc)
        return None

    # Response shape: ['garturlres', PUBLISHER_URL, 1, AMP_URL]
    if isinstance(inner, list) and len(inner) >= 2 and isinstance(inner[1], str):
        publisher = inner[1].strip()
        if publisher.startswith(("http://", "https://")):
            return publisher
    return None


async def resolve_google_news_url(
    url: str, *, client: httpx.AsyncClient | None = None
) -> str | None:
    """Resolve a Google News redirect URL to the underlying publisher URL.

    Strategy (in order):
        1. ``?url=`` query parameter (older legacy form).
        2. Base64 decode of the path component after ``/articles/`` — works
           only for the *very* old ``CBMi``-prefixed inline-URL format.
        3. Fetch the Google News page, parse ``data-n-a-sg`` / ``data-n-a-ts``
           / ``data-n-a-id`` tokens out of the HTML, then POST to Google's
           internal ``Fbv4je`` batchexecute RPC to get the publisher URL.
        4. Last-resort HTML scrape (canonical / meta refresh / data-n-au /
           location.replace) — these almost never work on modern Google News
           but are cheap fallbacks if step 3 returns empty.

    Returns ``None`` if every strategy fails. Never raises.
    """
    # 1. ?url= / ?u= query parameter
    try:
        qs = parse_qs(urlparse(url).query)
    except ValueError:
        qs = {}
    for key in ("url", "u"):
        values = qs.get(key)
        if values and values[0].startswith(("http://", "https://")):
            logger.info("Google News resolved via query: %s -> %s", url, values[0])
            return values[0]

    # 2. Base64-decoded path (old format)
    decoded = _decode_google_news_path(url)
    if decoded:
        logger.info("Google News resolved via base64 decode: %s -> %s", url, decoded)
        return decoded

    # 3. Fetch HTML, extract tokens, call batchexecute (modern format)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.info(
                "Google News page returned HTTP %s for %s", resp.status_code, url
            )
            return None

        html = resp.text
        sig_m = _GN_SIG_RE.search(html)
        ts_m = _GN_TS_RE.search(html)
        aid_m = _GN_AID_RE.search(html)

        if sig_m and ts_m and aid_m:
            publisher = await _resolve_via_batchexecute(
                aid_m.group(1), ts_m.group(1), sig_m.group(1), client
            )
            if publisher:
                logger.info(
                    "Google News resolved via batchexecute: %s -> %s",
                    url, publisher,
                )
                return publisher
            logger.info("batchexecute returned no publisher URL for %s", url)
        else:
            logger.info(
                "Could not find data-n-a-{sg,ts,id} tokens in HTML for %s "
                "(html_len=%d)", url, len(html),
            )

        # 4. Last-resort static HTML scrape
        scraped = _scrape_gnews_html_for_url(html[:200_000])
        if scraped:
            logger.info(
                "Google News resolved via HTML scrape (last-resort): %s -> %s",
                url, scraped,
            )
            return scraped
    except Exception as exc:
        logger.debug("Google News HTTP resolution failed for %s: %s", url, exc)
    finally:
        if owns_client and client is not None:
            await client.aclose()

    logger.info("Google News URL could not be resolved: %s", url)
    return None


# Back-compat shim — old code path that didn't make HTTP calls.
def _resolve_google_news_url(url: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
    except ValueError:
        return url
    for key in ("url", "u"):
        values = qs.get(key)
        if values and values[0].startswith(("http://", "https://")):
            return values[0]
    return url


# --------------------------------------------------------------------------- #
# Meta-tag extraction                                                         #
# --------------------------------------------------------------------------- #

def _collect_meta(html: str) -> dict[str, str]:
    """Return a {property/name: content} dict from <meta> tags."""
    meta: dict[str, str] = {}
    for m in _META_RE.finditer(html):
        key, value = m.group(1).strip().lower(), m.group(2).strip()
        if key and value and key not in meta:
            meta[key] = value
    for m in _META_RE_REV.finditer(html):
        value, key = m.group(1).strip(), m.group(2).strip().lower()
        if key and value and key not in meta:
            meta[key] = value
    return meta


def _meta_first(meta: dict[str, str], *keys: str) -> str | None:
    for k in keys:
        v = meta.get(k.lower())
        if v:
            return v
    return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    # Python's fromisoformat is fussy about the 'Z' suffix pre-3.11; replace.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# --------------------------------------------------------------------------- #
# Body extraction                                                             #
# --------------------------------------------------------------------------- #

# Site-specific main-content selectors (used by the fallback extractor).
_FALLBACK_MAIN_SELECTORS = (
    r"<article\b[^>]*>(.*?)</article>",
    r'<div[^>]+(?:id|class)=["\'][^"\']*(?:post-content|article-body|entry-content|story-body|article__body|article-content|prose|markdown-body)[^"\']*["\'][^>]*>(.*?)</div>',
    r"<main\b[^>]*>(.*?)</main>",
)


def _strip_unwanted_blocks(html: str) -> str:
    """Pre-strip ads / nav / footer / scripts before readability runs."""
    for pat in (
        r"<script\b[^>]*>.*?</script>",
        r"<style\b[^>]*>.*?</style>",
        r"<noscript\b[^>]*>.*?</noscript>",
        r"<nav\b[^>]*>.*?</nav>",
        r"<header\b[^>]*>.*?</header>",
        r"<footer\b[^>]*>.*?</footer>",
        r"<aside\b[^>]*>.*?</aside>",
        r"<form\b[^>]*>.*?</form>",
    ):
        html = re.sub(pat, "", html, flags=re.IGNORECASE | re.DOTALL)
    return html


def _fallback_extract_body(html: str) -> str:
    """Secondary extractor for pages readability strips empty.

    Walks a list of common ``<article>``/main-content selectors and returns
    the first match's text — converted to markdown.
    """
    cleaned = _strip_unwanted_blocks(html)
    for pat in _FALLBACK_MAIN_SELECTORS:
        m = re.search(pat, cleaned, re.IGNORECASE | re.DOTALL)
        if m:
            inner = m.group(1)
            if len(inner) >= 200:
                return inner
    return ""


def _html_to_markdown(html: str) -> str:
    """Convert page HTML to clean markdown via readability + markdownify.

    If readability returns an empty body (common on JS-heavy pages or some
    modern publisher templates), retry against a manually-extracted
    ``<article>``/main block.
    """
    if not html:
        return ""

    pre = _strip_unwanted_blocks(html)

    clean_html = ""
    try:
        article = readability_parse(pre)
        clean_html = article.content or ""
    except Exception:
        logger.debug("readability_parse failed, using raw HTML body")
        clean_html = ""

    # Fallback when readability returns nothing usable.
    if not clean_html or len(clean_html.strip()) < 200:
        fallback = _fallback_extract_body(html)
        if len(fallback) > len(clean_html):
            clean_html = fallback

    if not clean_html or not clean_html.strip():
        return ""

    markdown = md(
        clean_html,
        heading_style="ATX",
        strip=["script", "style"],
    )
    markdown = _BLANK_LINES_RE.sub("\n\n", markdown)
    markdown = _TRAILING_SPACE_RE.sub("", markdown)
    return markdown.strip()


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

async def fetch_article(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch ``url`` and return a structured extraction result.

    Always returns a dict; on total failure it contains
    ``{"resolved_url": url}`` and nothing else.
    """
    if not url:
        return {}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0),
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )

    # If this is a Google News URL, resolve to the real publisher URL FIRST.
    # We never want to extract content from news.google.com itself.
    pre_resolved = url
    if _is_google_news_url(url):
        resolved = await resolve_google_news_url(url, client=client)
        if resolved:
            pre_resolved = resolved
        else:
            logger.info(
                "Skipping article fetch — Google News URL could not be resolved: %s",
                url,
            )
            if owns_client and client is not None:
                await client.aclose()
            return {"resolved_url": url}

    result: dict[str, Any] = {"resolved_url": pre_resolved}

    try:
        resp = await client.get(pre_resolved)
        result["resolved_url"] = str(resp.url)
        if resp.status_code != 200:
            logger.info(
                "Article fetch returned HTTP %s for url=%s (resolved=%s)",
                resp.status_code, url, pre_resolved,
            )
            return result

        ctype = resp.headers.get("content-type", "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            logger.info(
                "Article fetch got non-HTML content-type=%s url=%s", ctype, url
            )
            return result

        html = resp.text[:_MAX_BYTES]
        final_url = str(resp.url)

        # Edge case: if we still landed on Google News (resolver returned
        # us back here), bail out — we won't get article content from it.
        if "news.google.com" in final_url:
            logger.info(
                "Article fetch landed on news.google.com — aborting body "
                "extraction for url=%s",
                url,
            )
            return result

        meta = _collect_meta(html)

        # Image — reuse the og:image / twitter:image / content-img logic.
        meta_img = _extract_meta_image(html)
        jsonld_img = _extract_jsonld_image(html)
        content_img = _best_content_image(html)
        # Content body image first (matches what readers see first), then
        # og:image, then JSON-LD. Logo og:images are skipped.
        if content_img:
            image_candidate = content_img
        elif meta_img and not _looks_like_site_logo(meta_img):
            image_candidate = meta_img
        elif jsonld_img and not _looks_like_site_logo(jsonld_img):
            image_candidate = jsonld_img
        else:
            image_candidate = None
        result["image_url"] = _validate_url(image_candidate, final_url)

        # Title — og:title preferred, then <title>.
        title = _meta_first(meta, "og:title", "twitter:title")
        if not title:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
        result["title"] = title or None

        # Description / summary
        result["description"] = _meta_first(
            meta, "og:description", "twitter:description", "description"
        )

        # Author
        result["author"] = _meta_first(
            meta, "article:author", "author", "twitter:creator"
        )

        # Published date — try several ISO-formatted meta keys
        published_raw = _meta_first(
            meta,
            "article:published_time",
            "article:published",
            "og:article:published_time",
            "datepublished",
            "pubdate",
        )
        result["published_at"] = _parse_iso_datetime(published_raw)

        # Body
        body = _html_to_markdown(html)
        result["body_markdown"] = body

        logger.info(
            "Article fetched: original=%s resolved=%s body_len=%d image=%s title=%r",
            url,
            final_url,
            len(body),
            result.get("image_url"),
            (title or "")[:60],
        )
        return result
    except Exception as exc:
        logger.debug("fetch_article failed for %s: %s", url, exc)
        return result
    finally:
        if owns_client and client is not None:
            await client.aclose()
