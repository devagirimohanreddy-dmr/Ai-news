"""SummarizeStage — generates a concise summary and headline via LLM."""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.article import Article
from src.models.summary import Summary
from src.pipeline.base import PipelineStage

# Sanitization regexes for the fallback summary path. We never want raw
# HTML, anchor tags, or bare URLs ending up in user-visible summary text.
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
_WHITESPACE_RE = re.compile(r"\s+")

logger = logging.getLogger(__name__)

SUMMARIZE_SYSTEM_PROMPT: str = (
    "Summarize this article in 2-3 sentences for a technical AI audience. "
    "Focus on what's new and why it matters. Also provide a one-line headline. "
    'Respond in JSON: {"summary": "...", "headline": "..."}'
)


class SummarizeStage(PipelineStage):
    """Fifth pipeline stage: produce a summary and headline.

    Sends the full markdown content to the LLM and persists a ``Summary``
    row linked to the article.  If the LLM is unavailable or fails, the
    stage creates a best-effort summary from the first few sentences of the
    content.
    """

    def __init__(self, session: AsyncSession, llm_router=None, **kwargs):
        self.session = session
        self.llm_router = llm_router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, article: Article) -> Article | None:
        headline: str | None = None
        summary_text: str | None = None
        llm_provider: str = "fallback"

        if self.llm_router is not None:
            headline, summary_text, llm_provider = await self._summarize_via_llm(
                article
            )

        # Fallback: extract the first 2-3 sentences
        if not summary_text:
            headline, summary_text = self._fallback_summary(article)
            llm_provider = "fallback"

        # Persist
        summary = Summary(
            article_id=article.id,
            headline=headline,
            summary_text=summary_text,
            llm_provider=llm_provider,
        )
        self.session.add(summary)

        article.pipeline_status = "summarized"
        await self.session.flush()

        logger.info(
            "Article id=%s summarized (provider=%s) headline=%r",
            article.id,
            llm_provider,
            (headline or "")[:60],
        )
        return article

    # ------------------------------------------------------------------
    # LLM summarization
    # ------------------------------------------------------------------

    async def _summarize_via_llm(
        self, article: Article
    ) -> tuple[str | None, str | None, str]:
        """Return (headline, summary_text, provider_name) or (None, None, '') on failure."""
        content = article.markdown_content or article.raw_content or ""

        try:
            response = await self.llm_router.generate(
                prompt=content,
                system_prompt=SUMMARIZE_SYSTEM_PROMPT,
                json_mode=True,
                task_type="summarize",
            )
            data = json.loads(response.text)

            summary_text = data.get("summary", "")
            headline = data.get("headline", "")

            if summary_text and isinstance(summary_text, str):
                return (
                    headline if isinstance(headline, str) else None,
                    summary_text,
                    response.provider,
                )

            logger.warning(
                "LLM returned empty summary for article id=%s", article.id
            )
            return None, None, ""

        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse LLM summary JSON for article id=%s",
                article.id,
                exc_info=True,
            )
            return None, None, ""
        except Exception:
            logger.warning(
                "LLM summarization failed for article id=%s",
                article.id,
                exc_info=True,
            )
            return None, None, ""

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_summary(article: Article) -> tuple[str, str]:
        """Extract headline from title and first sentences from content.

        Output is always plain text — HTML tags, anchor markup, and bare
        URLs are stripped so the summary box never displays raw HTML or a
        Google News redirect link.
        """
        headline = (article.title or "No title").strip()
        if len(headline) > 512:
            headline = headline[:509] + "..."

        # Prefer cleaned markdown; fall back to raw_content only if we
        # must — and aggressively sanitize whatever we use.
        source = article.markdown_content or article.raw_content or ""

        # Strip markdown link wrappers first: "[text](url)" -> "text".
        text = _MD_LINK_RE.sub(r"\1", source)
        # Strip every HTML tag.
        text = _TAG_RE.sub(" ", text)
        # Drop every bare URL.
        text = _URL_RE.sub("", text)
        # Drop markdown headings (#, ##, ...).
        text = re.sub(r"(?m)^#{1,6}\s*", "", text)
        # Collapse whitespace.
        text = _WHITESPACE_RE.sub(" ", text).strip()

        if not text:
            # All we had was URL / markup. Use the title as the summary
            # rather than serving the user a broken link.
            return headline, headline

        # Take the first ~3 sentences by splitting on terminal punctuation.
        for sep in (".", "!", "?"):
            text = text.replace(sep, sep + "|||")
        parts = [s.strip() for s in text.split("|||") if s.strip()]
        sentences = parts[:3]

        summary_text = " ".join(sentences) if sentences else text
        if len(summary_text) > 1000:
            summary_text = summary_text[:997] + "..."

        return headline, summary_text
