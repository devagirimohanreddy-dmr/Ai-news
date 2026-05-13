"""Pick the best representative image for an article webpage.

Resolution order:
    1. Site-specific handlers (arxiv, reddit, etc.)
    2. ``<meta property="og:image">``   (skipped if it looks like a site logo)
    3. ``<meta name="twitter:image">``
    4. JSON-LD schema.org ``image`` field
    5. ``<link rel="image_src">``
    6. The largest ``<img>`` found inside the article body
       (``<article>``/``<main>``/common content wrappers), filtered to skip
       icons, sprites, logos, and trivially-small images
    7. Same heuristic over the whole document as a last resort

Returned URLs are resolved against the response URL and validated.
"""

from __future__ import annotations

import html as _html_lib
import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# ── Meta tags ────────────────────────────────────────────────────────────
_META_PATTERNS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(
            r'<meta[^>]+(?:property|name)=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']',
            re.IGNORECASE,
        ),
        re.compile(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::url)?["\']',
            re.IGNORECASE,
        ),
    ),
    (
        re.compile(
            r'<meta[^>]+(?:property|name)=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            re.IGNORECASE,
        ),
        re.compile(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']twitter:image(?::src)?["\']',
            re.IGNORECASE,
        ),
    ),
)

_LINK_IMAGE_SRC_RE = re.compile(
    r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# ── <img> matching ───────────────────────────────────────────────────────
# Capture the whole tag so we can inspect attributes (width/height/src/srcset).
_IMG_TAG_FULL_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r'\b([a-zA-Z\-:]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
    re.IGNORECASE,
)

# Patterns that mean "this is clearly decorative chrome, not article content".
# Conservative — only reject if we're confident. A legitimate hero image
# called "header.jpg" should NOT be skipped just because it contains "header".
_SKIP_IMG_PATTERNS = re.compile(
    r"(?:"
    r"(?:^|/|_|-)1x1\.(?:gif|png)|"            # 1x1 trackers
    r"(?:^|/)pixel\.(?:gif|png)|"
    r"(?:^|/|_|-)spacer\.(?:gif|png)|"
    r"(?:^|/|_|-)blank\.(?:gif|png)|"
    r"/(?:tracking|tracker|analytics)/|"
    r"/sprites?/|"
    r"/icons?/|/icon-sprite/|"                 # icon directories (UI chrome)
    r"/(?:favicon|favicons)/|favicon\.|"
    r"(?:^|/|_|-)gravatar(?:\.com)?|"
    r"emoji/|/emojis/|"
    r"data:image"
    r")",
    re.IGNORECASE,
)
_SKIP_HOST_PATTERNS = re.compile(
    r"(?:doubleclick|googlesyndication|googletagmanager|google-analytics|"
    r"adservice|adsystem|cloudflareinsights|hotjar|segment\.io|amplitude)",
    re.IGNORECASE,
)

# Likely main-content blocks, in priority order. Search inside these first.
_CONTENT_BLOCK_PATTERNS = (
    re.compile(r"<article\b[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL),
    re.compile(
        r'<(?:div|section)\b[^>]*(?:id|class)=["\'][^"\']*(?:'
        r"post-content|article-body|article__body|entry-content|story-body|"
        r"article-content|markdown-body|prose|content-body|post-body|"
        r"entry|abstract|paper-body|content__body|main-content"
        r')[^"\']*["\'][^>]*>(.*?)</(?:div|section)>',
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"<main\b[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL),
)

# Minimum dimensions for an image to count as a hero. Only filters when the
# tag has explicit width/height — we accept images without dimensions.
_MIN_HERO_PIXELS = 120

# Max HTML bytes to scan. Some publisher templates push article content
# below 500KB+ of inline JS / preview cards, so we read generously.
_MAX_BYTES = 800_000

# Look enough like a real browser to satisfy most publisher anti-bot rules.
# We're identifying as a recent Chrome on Windows because that's what most
# servers expect; bot-flagged UAs ("AI-News-Bot/1.0") were getting blocked
# with 401/403/429 by WSJ, NYT, OpenAI, PCMag, Venturebeat, etc.
_BROWSER_HEADERS: dict[str, str] = {
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


# ─────────────────────────────────────────────────────────────────────────


def _validate_url(url: str | None, base_url: str) -> str | None:
    """Resolve relative URLs against base_url and validate the result.

    Decodes HTML entities (``&amp;`` → ``&`` etc.) so URLs scraped from raw
    HTML attributes work in browsers without further unescape.
    """
    if not url:
        return None
    url = _html_lib.unescape(url.strip())
    if not url:
        return None
    resolved = urljoin(base_url, url)
    try:
        parsed = urlparse(resolved)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    return resolved


def _parse_attrs(tag: str) -> dict[str, str]:
    """Return a {attr: value} map from an HTML tag string."""
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag):
        name = m.group(1).lower()
        value = m.group(2) or m.group(3) or m.group(4) or ""
        attrs[name] = value
    return attrs


def _is_decorative_url(src: str) -> bool:
    """Quick check: does the URL look like a logo, sprite, tracker, etc.?"""
    if not src or src.startswith("data:"):
        return True
    if _SKIP_IMG_PATTERNS.search(src):
        return True
    if _SKIP_HOST_PATTERNS.search(src):
        return True
    return False


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.match(r"\d+", value.strip())
    return int(m.group(0)) if m else None


def _img_dimensions(attrs: dict[str, str]) -> tuple[int | None, int | None]:
    """Pull pixel width / height from the tag, including a few common style hints."""
    w = _to_int(attrs.get("width"))
    h = _to_int(attrs.get("height"))
    style = attrs.get("style", "")
    if style:
        sw = re.search(r"width\s*:\s*(\d+)\s*px", style, re.IGNORECASE)
        sh = re.search(r"height\s*:\s*(\d+)\s*px", style, re.IGNORECASE)
        if sw and w is None:
            w = int(sw.group(1))
        if sh and h is None:
            h = int(sh.group(1))
    return w, h


def _pick_src(attrs: dict[str, str]) -> str | None:
    """Choose the best ``src`` candidate from an <img> tag.

    Prefers ``srcset``'s largest entry, then ``data-src`` / ``data-original``
    (common lazy-load patterns), then ``src``.
    """
    # srcset — pick the entry with the largest declared width
    srcset = attrs.get("srcset") or attrs.get("data-srcset")
    if srcset:
        best_w = -1
        best_url: str | None = None
        for entry in srcset.split(","):
            parts = entry.strip().split()
            if not parts:
                continue
            url = parts[0]
            w = -1
            if len(parts) >= 2 and parts[1].endswith("w"):
                try:
                    w = int(parts[1].rstrip("w"))
                except ValueError:
                    w = -1
            if w > best_w:
                best_w = w
                best_url = url
        if best_url:
            return best_url

    for key in ("data-src", "data-original", "data-lazy-src", "src"):
        v = attrs.get(key)
        if v:
            return v
    return None


def _extract_meta_image(html: str) -> str | None:
    """Try each meta-tag pattern set in priority order."""
    for re_a, re_b in _META_PATTERNS:
        m = re_a.search(html) or re_b.search(html)
        if m:
            candidate = m.group(1).strip()
            if candidate:
                return candidate
    m = _LINK_IMAGE_SRC_RE.search(html)
    if m:
        return m.group(1).strip()
    return None


# JSON-LD / schema.org parsing — many news sites and blogs embed structured
# data with an ``image`` field that's the canonical hero.
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _walk_jsonld_image(node) -> str | None:
    """Recursively search a JSON-LD payload for an ``image`` URL."""
    if isinstance(node, dict):
        # Direct image field
        img = node.get("image")
        if img:
            if isinstance(img, str):
                return img
            if isinstance(img, dict):
                u = img.get("url") or img.get("@id") or img.get("contentUrl")
                if isinstance(u, str):
                    return u
                nested = _walk_jsonld_image(img)
                if nested:
                    return nested
            if isinstance(img, list) and img:
                first = img[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    u = first.get("url") or first.get("@id") or first.get("contentUrl")
                    if isinstance(u, str):
                        return u
        # Walk nested objects (e.g. ``mainEntity`` containing the article).
        for key in ("mainEntity", "mainEntityOfPage", "@graph"):
            v = node.get(key)
            if v is not None:
                nested = _walk_jsonld_image(v)
                if nested:
                    return nested
    elif isinstance(node, list):
        for item in node:
            nested = _walk_jsonld_image(item)
            if nested:
                return nested
    return None


def _extract_jsonld_image(html: str) -> str | None:
    """Find an ``image`` URL inside any ``<script type="application/ld+json">``."""
    for m in _JSONLD_RE.finditer(html):
        block = m.group(1).strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        result = _walk_jsonld_image(data)
        if result:
            return result
    return None


def _iter_imgs(html: str, *, debug: list | None = None):
    """Yield ``(src, area)`` for each useful ``<img>`` in the given HTML.

    ``area`` is ``width*height`` when both are known, else 0.  Decorative
    images (logos, icons, etc.) and obvious thumbnails are skipped.

    If a ``debug`` list is passed, each candidate is recorded as a
    ``(src, status, reason)`` tuple — useful for logging which images were
    seen and why each was kept or rejected.
    """
    for m in _IMG_TAG_FULL_RE.finditer(html):
        attrs = _parse_attrs(m.group(0))
        src = _pick_src(attrs)
        if not src:
            if debug is not None:
                debug.append(("(no src)", "skip", "no_src"))
            continue
        if _is_decorative_url(src):
            if debug is not None:
                debug.append((src, "skip", "decorative_url"))
            continue
        w, h = _img_dimensions(attrs)
        if w is not None and w < _MIN_HERO_PIXELS:
            if debug is not None:
                debug.append((src, "skip", f"width<{_MIN_HERO_PIXELS}({w})"))
            continue
        if h is not None and h < _MIN_HERO_PIXELS:
            if debug is not None:
                debug.append((src, "skip", f"height<{_MIN_HERO_PIXELS}({h})"))
            continue
        area = (w or 0) * (h or 0)
        if debug is not None:
            debug.append((src, "accept", f"area={area}"))
        yield src, area


def _best_content_image(html: str, *, debug: list | None = None) -> str | None:
    """Find the largest non-decorative image inside the main content blocks.

    Falls back to scanning the entire document if no content block is found.
    """
    candidates: list[tuple[str, int, int]] = []
    for priority, pattern in enumerate(_CONTENT_BLOCK_PATTERNS):
        m = pattern.search(html)
        if not m:
            continue
        for src, area in _iter_imgs(m.group(1), debug=debug):
            candidates.append((src, area, priority))
        if candidates:
            break

    if not candidates:
        # Last resort: whole document
        for src, area in _iter_imgs(html, debug=debug):
            candidates.append((src, area, len(_CONTENT_BLOCK_PATTERNS)))

    if not candidates:
        return None

    # Within the highest-priority block (lowest priority number) pick the
    # largest image; ties go to document order (which we get for free since
    # we appended in order).
    candidates.sort(key=lambda t: (t[2], -t[1]))
    return candidates[0][0]


def _looks_like_site_logo(url: str | None) -> bool:
    """Conservative heuristic: og:image paths that are clearly NOT an article hero.

    Only matches the path **filename** (segment after the last ``/``) so
    legitimate hero images on a CDN whose path happens to contain the
    string "logo" somewhere (e.g. ``/blogo-photo.jpg``) aren't rejected.
    """
    if not url:
        return False
    path = urlparse(url).path.lower()
    last = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(png|jpg|jpeg|gif|webp|svg)$", "", last)

    # Match "logo" / "favicon" as a discrete token in the filename — at the
    # start, end, or set off by separators. This catches ``logo.svg``,
    # ``arxiv-logo.png``, ``arxiv-logo-fb.png``, ``site_favicon.ico``.
    if re.search(r"(?:^|[-_.])(?:logo|favicon)(?:[-_.]|$)", stem):
        return True

    # Publisher fallback graphics — common explicit fingerprints.
    if stem in ("og-default", "default", "default-share", "default-image",
                "default-promo", "social-share", "share-card", "share-image",
                "placeholder", "fallback", "generic", "generic-share",
                "brand-image"):
        return True
    if "defaultpromo" in stem or "promocrop" in stem:
        return True
    if stem.endswith("-fb"):
        return True
    # NYT-style "1200x675_nameplate" and similar generic share templates.
    if re.search(r"(?:^|[-_])nameplate(?:[-_.]|$)", stem):
        return True
    if re.search(r"\d{3,4}x\d{3,4}[-_]?nameplate", stem):
        return True
    return False


# ── Playwright (headless Chromium) fallback ─────────────────────────────


async def _playwright_extract_image(url: str) -> str | None:
    """Open ``url`` in a headless Chromium and extract the first big content
    image after JS finishes rendering.

    Used as a last resort for sites that return 401/403/429 to plain HTTP
    fetches or that ship empty HTML and inject images via client-side JS.

    Never raises. Returns ``None`` on any failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.debug("playwright not available; skipping browser fallback")
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=_BROWSER_HEADERS["User-Agent"],
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    # Give the page a moment for above-the-fold images.
                    try:
                        await page.wait_for_load_state("networkidle", timeout=4_000)
                    except Exception:
                        pass  # don't fail if network never idles

                    # Find the largest visible content image. Do NOT fall back
                    # to og:image inside JS — that's already been tried by the
                    # static fetch path, and triggering it here often returns
                    # a site-wide logo that pollutes the DB.
                    img_url = await page.evaluate(
                        r"""
                        () => {
                            const blocks = document.querySelectorAll(
                                'article, main, [class*="post-content"], [class*="article-body"], ' +
                                '[class*="entry-content"], [class*="story-body"], ' +
                                '[class*="article-content"], [class*="markdown-body"], ' +
                                '[class*="prose"], [class*="post-body"]'
                            );
                            const roots = blocks.length ? Array.from(blocks) : [document.body];
                            let best = null, bestArea = 0;
                            for (const root of roots) {
                                const imgs = root.querySelectorAll('img');
                                for (const img of imgs) {
                                    const src = img.currentSrc || img.src;
                                    if (!src || src.startsWith('data:')) continue;
                                    if (/logo|favicon|sprite|gravatar|emoji|\/icons?\//i.test(src)) continue;
                                    const r = img.getBoundingClientRect();
                                    const w = r.width || img.naturalWidth || 0;
                                    const h = r.height || img.naturalHeight || 0;
                                    if (w < 200 || h < 120) continue;
                                    const area = w * h;
                                    if (area > bestArea) {
                                        bestArea = area;
                                        best = src;
                                    }
                                }
                                if (best) break; // first matching content block wins
                            }
                            return best;
                        }
                        """
                    )
                    # Server-side belt-and-braces: also reject logos here.
                    if img_url and _looks_like_site_logo(img_url):
                        return None
                    return img_url
                finally:
                    await page.close()
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        logger.info("Playwright fetch failed for %s: %s", url, exc)
        return None


# ── Site-specific handlers ───────────────────────────────────────────────


_ARXIV_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?")


async def _arxiv_image(url: str, client: httpx.AsyncClient) -> str | None:
    """Try to pull a figure from the ``ar5iv`` HTML rendering of an arxiv paper.

    Arxiv abstract pages have no inline figures, but ar5iv labs (and the
    newer ``browse.arxiv.org/html/`` path) render papers as HTML with their
    actual figures. We try ar5iv first because it has been around longer.
    """
    m = _ARXIV_ID_RE.search(url)
    if not m:
        return None
    paper_id = m.group(1)
    for candidate_url in (
        f"https://ar5iv.labs.arxiv.org/html/{paper_id}",
        f"https://browse.arxiv.org/html/{paper_id}",
    ):
        try:
            resp = await client.get(candidate_url)
            if resp.status_code != 200:
                continue
            html = resp.text[:_MAX_BYTES]
            content_img = _best_content_image(html)
            if content_img:
                logger.info(
                    "arxiv: resolved figure via %s -> %s",
                    candidate_url, content_img,
                )
                return _validate_url(content_img, candidate_url)
        except Exception as exc:
            logger.debug("arxiv html fetch failed for %s: %s", candidate_url, exc)
    return None


async def _reddit_image(url: str, client: httpx.AsyncClient) -> str | None:
    """Pull the preview image for a reddit post via the public ``.json`` API.

    Returns ``None`` for text-only posts or when the API blocks us.
    """
    if "reddit.com" not in url:
        return None
    json_url = url.rstrip("/") + "/.json"
    try:
        resp = await client.get(
            json_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AINewsBot/1.0)"},
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except Exception as exc:
        logger.debug("reddit json fetch failed for %s: %s", json_url, exc)
        return None

    # Listing payload: [{"data":{"children":[{"data":{...post...}}]}}, ...]
    try:
        post = payload[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None

    # Possible places the image lives, in priority order.
    preview = post.get("preview", {})
    images = preview.get("images") if isinstance(preview, dict) else None
    if isinstance(images, list) and images:
        src = images[0].get("source", {}).get("url")
        if isinstance(src, str):
            # Reddit returns URLs with HTML entities. unescape happens in
            # ``_validate_url`` later.
            return src

    thumb = post.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith(("http://", "https://")):
        return thumb

    return None


# ─────────────────────────────────────────────────────────────────────────


async def fetch_article_image(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    allow_browser: bool = True,
) -> str | None:
    """Fetch the page at ``url`` and return its best representative image URL.

    Returns ``None`` if the page can't be fetched or no usable image is found.
    Never raises — image extraction is best-effort.

    Extraction order:
        1. Site-specific handlers (arxiv, reddit, ...)
        2. Static HTTP fetch:
           a. First large image inside article body (content)
           b. og:image (if not a logo)
           c. JSON-LD schema.org image
        3. Playwright headless-Chromium fallback for sites that gate static
           fetches (set ``allow_browser=False`` to disable).
    """
    if not url:
        return None

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0),
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )

    static_failure_reason: str | None = None

    try:
        # ── Site-specific shortcuts first ──────────────────────────────
        host = urlparse(url).netloc.lower()
        if host.endswith("arxiv.org"):
            arxiv = await _arxiv_image(url, client)
            if arxiv:
                logger.info("Image extraction url=%s strategy=arxiv -> %s", url, arxiv)
                return arxiv
        if host.endswith("reddit.com"):
            reddit = await _reddit_image(url, client)
            if reddit:
                # Strip HTML entities and validate.
                validated = _validate_url(reddit, url)
                logger.info("Image extraction url=%s strategy=reddit -> %s", url, validated)
                return validated

        # ── Generic flow ───────────────────────────────────────────────
        try:
            resp = await client.get(url)
        except Exception as exc:
            static_failure_reason = f"http_error:{type(exc).__name__}"
            resp = None
        if resp is None or resp.status_code != 200:
            static_failure_reason = static_failure_reason or f"http_{getattr(resp, 'status_code', 'noresp')}"
            if allow_browser:
                logger.info(
                    "Image extraction url=%s static fetch failed (%s); trying browser",
                    url, static_failure_reason,
                )
                img = await _playwright_extract_image(url)
                if img:
                    validated = _validate_url(img, url)
                    logger.info(
                        "Image extraction url=%s strategy=browser -> %s",
                        url, validated,
                    )
                    return validated
            logger.info(
                "Image extraction url=%s strategy=none reason=%s",
                url, static_failure_reason,
            )
            return None
        ctype = resp.headers.get("content-type", "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            return None

        html = resp.text[:_MAX_BYTES]
        base = str(resp.url)

        debug_candidates: list = []
        meta_img = _extract_meta_image(html)
        jsonld_img = _extract_jsonld_image(html)
        content_img = _best_content_image(html, debug=debug_candidates)
        meta_is_logo = bool(meta_img and _looks_like_site_logo(meta_img))
        jsonld_is_logo = bool(jsonld_img and _looks_like_site_logo(jsonld_img))

        # Priority chain (per spec — match the first image users see when
        # opening the article):
        #   1. First large image inside article body / main content
        #   2. og:image (if not a logo)
        #   3. JSON-LD image (if not a logo)
        if content_img:
            chosen = content_img
            strategy = "content-img"
        elif meta_img and not meta_is_logo:
            chosen = meta_img
            strategy = "og:image"
        elif jsonld_img and not jsonld_is_logo:
            chosen = jsonld_img
            strategy = "json-ld"
        else:
            chosen = None
            strategy = "none"

        validated = _validate_url(chosen, base)

        logger.info(
            "Image extraction url=%s strategy=%s meta=%s jsonld=%s content=%s "
            "chosen=%s candidates_seen=%d",
            url, strategy,
            (meta_img or "")[:80],
            (jsonld_img or "")[:80],
            (content_img or "")[:80],
            (validated or "")[:80],
            len(debug_candidates),
        )
        if validated is None and debug_candidates:
            for src, status, reason in debug_candidates[:5]:
                logger.info("  candidate %s (%s) src=%s", status, reason, src[:100])

        # Last-resort: render in headless Chromium for sites that gate
        # images behind JS (modern SPAs, paywall preview banners, etc.)
        if validated is None and allow_browser:
            logger.info(
                "Image extraction url=%s static returned nothing; trying browser",
                url,
            )
            img = await _playwright_extract_image(url)
            if img:
                validated = _validate_url(img, base)
                logger.info(
                    "Image extraction url=%s strategy=browser -> %s",
                    url, validated,
                )

        return validated
    except Exception as exc:
        logger.debug("Image fetch failed for %s: %s", url, exc)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()
