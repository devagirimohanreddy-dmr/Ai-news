"""Post-processor that converts messy HTML to clean markdown.

Applied after scrapers that return HTML (RSS, API, Playwright).
Firecrawl already outputs markdown so it skips this step.
"""

from __future__ import annotations

import logging
import re

from markdownify import markdownify as md
from readability import parse as readability_parse

logger = logging.getLogger(__name__)


class ContentCleaner:
    """Strips ads, nav, boilerplate from HTML and converts to clean markdown."""

    # Minimum length (characters) for input to be treated as HTML worth processing.
    _MIN_HTML_LENGTH = 20

    # Regex to detect whether input looks like HTML (contains at least one tag).
    _HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")

    # Patterns used during whitespace normalisation.
    _BLANK_LINES_RE = re.compile(r"\n{3,}")
    _TRAILING_SPACES_RE = re.compile(r"[ \t]+$", re.MULTILINE)

    @staticmethod
    def clean(html_content: str) -> str:
        """Convert HTML to clean markdown.

        Uses *python-readability* to extract the main article content, then
        *markdownify* to produce ATX-style markdown.

        Edge cases handled:
        - Empty / whitespace-only input returns an empty string.
        - Input that does not look like HTML is returned as-is (plain text).
        - HTML with no readable body text returns an empty string.
        """
        if not html_content or not html_content.strip():
            return ""

        # If the content is too short or contains no HTML tags, treat it as
        # plain text and return unchanged.
        stripped = html_content.strip()
        if (
            len(stripped) < ContentCleaner._MIN_HTML_LENGTH
            or not ContentCleaner._HTML_TAG_RE.search(stripped)
        ):
            return stripped

        # --- 1. Extract main article content with readability ---------------
        try:
            article = readability_parse(html_content)
            clean_html = article.content or ""
        except Exception:
            logger.debug("Readability failed; falling back to raw HTML")
            clean_html = html_content

        if not clean_html or not clean_html.strip():
            return ""

        # --- 2. Convert clean HTML to markdown ------------------------------
        markdown = md(
            clean_html,
            heading_style="ATX",
            strip=["img", "script", "style"],
        )

        # --- 3. Normalise whitespace ----------------------------------------
        markdown = ContentCleaner._BLANK_LINES_RE.sub("\n\n", markdown)
        markdown = ContentCleaner._TRAILING_SPACES_RE.sub("", markdown)
        markdown = markdown.strip()

        # If readability + markdownify produced nothing useful, return empty.
        if not markdown:
            return ""

        return markdown

    @staticmethod
    def extract_title(html_content: str) -> str:
        """Extract article title from HTML using readability.

        Returns an empty string when the title cannot be determined.
        """
        if not html_content or not html_content.strip():
            return ""

        try:
            article = readability_parse(html_content)
            title = article.title
            return title.strip() if title else ""
        except Exception:
            logger.debug("Failed to extract title from HTML")
            return ""
