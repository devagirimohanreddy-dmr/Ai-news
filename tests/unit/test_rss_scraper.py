"""Unit tests for the RSS / Atom feed scraper."""

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.scrapers.base import RawArticle
from src.scrapers.rss_scraper import RssScraper


# ---------------------------------------------------------------------------
# Helpers — build feedparser-like result objects
# ---------------------------------------------------------------------------

def _make_entry(
    title: str = "Test Article",
    link: str = "https://example.com/article",
    summary: str = "Article summary text.",
    published_parsed: time.struct_time | None = None,
    author: str | None = "Jane Doe",
    tags: list[dict] | None = None,
    content: list[dict] | None = None,
):
    """Return a dict that mimics a ``feedparser`` entry."""
    entry = SimpleNamespace(
        title=title,
        link=link,
        summary=summary,
        author=author,
        author_detail=None,
    )
    if published_parsed is not None:
        entry.published_parsed = published_parsed
    if tags is not None:
        entry.tags = tags
    if content is not None:
        entry.content = content
    return entry


def _make_feed(entries, *, bozo: bool = False, bozo_exception=None, feed_title: str = "My Feed"):
    """Return an object that mimics a parsed ``feedparser`` feed."""
    feed = {
        "entries": entries,
        "feed": {"title": feed_title},
        "bozo": int(bozo),
    }
    if bozo_exception is not None:
        feed["bozo_exception"] = bozo_exception
    return feed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def source_config():
    return {"feed_url": "https://example.com/feed.xml", "source_name": "Example"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRssScraperWellFormed:
    """Parsing a well-formed feed with all fields present."""

    @pytest.mark.asyncio
    async def test_returns_raw_articles(self, source_config):
        published = time.strptime("2025-06-15 10:00:00", "%Y-%m-%d %H:%M:%S")
        entries = [
            _make_entry(
                title="First Post",
                link="https://example.com/1",
                summary="Summary one.",
                published_parsed=published,
                author="Alice",
                tags=[{"term": "AI"}, {"term": "ML"}],
            ),
            _make_entry(
                title="Second Post",
                link="https://example.com/2",
                summary="Summary two.",
                published_parsed=published,
                author="Bob",
            ),
        ]
        mock_feed = _make_feed(entries)

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert len(articles) == 2

        first = articles[0]
        assert isinstance(first, RawArticle)
        assert first.title == "First Post"
        assert first.url == "https://example.com/1"
        assert first.raw_content == "Summary one."
        assert first.author == "Alice"
        assert first.source_name == "Example"
        assert first.published_at is not None
        assert isinstance(first.published_at, datetime)
        assert first.metadata["tags"] == ["AI", "ML"]
        assert first.metadata["feed_title"] == "My Feed"

    @pytest.mark.asyncio
    async def test_prefers_content_over_summary(self, source_config):
        """When an entry has both ``content`` and ``summary``, content wins."""
        entry = _make_entry(
            summary="Short summary.",
            content=[{"value": "<p>Full HTML content.</p>"}],
        )
        mock_feed = _make_feed([entry])

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert articles[0].raw_content == "<p>Full HTML content.</p>"


class TestRssScraperMissingFields:
    """Entries that lack optional fields like author or published date."""

    @pytest.mark.asyncio
    async def test_missing_author(self, source_config):
        entry = _make_entry(author=None)
        mock_feed = _make_feed([entry])

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert articles[0].author is None

    @pytest.mark.asyncio
    async def test_missing_published_date(self, source_config):
        entry = _make_entry()
        # Ensure no date attributes exist
        assert not hasattr(entry, "published_parsed")
        mock_feed = _make_feed([entry])

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert articles[0].published_at is None

    @pytest.mark.asyncio
    async def test_missing_author_and_date(self, source_config):
        entry = _make_entry(author=None)
        mock_feed = _make_feed([entry])

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert articles[0].author is None
        assert articles[0].published_at is None

    @pytest.mark.asyncio
    async def test_entry_without_link_is_skipped(self, source_config):
        entry = _make_entry(link="")
        mock_feed = _make_feed([entry])

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert articles == []


class TestRssScraperMalformed:
    """Malformed or broken feeds should return an empty list, not raise."""

    @pytest.mark.asyncio
    async def test_bozo_feed_no_entries_returns_empty(self, source_config):
        mock_feed = _make_feed([], bozo=True, bozo_exception=Exception("bad XML"))

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_bozo_feed_with_entries_still_returns_them(self, source_config):
        """A bozo feed that still has parseable entries should yield results."""
        entry = _make_entry(title="Salvaged Post", link="https://example.com/salvaged")
        mock_feed = _make_feed([entry], bozo=True, bozo_exception=Exception("minor issue"))

        with patch("src.scrapers.rss_scraper.feedparser.parse", return_value=mock_feed):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert articles[0].title == "Salvaged Post"

    @pytest.mark.asyncio
    async def test_network_exception_returns_empty(self, source_config):
        with patch(
            "src.scrapers.rss_scraper.feedparser.parse",
            side_effect=Exception("connection refused"),
        ):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self, source_config):
        """If the feed fetch exceeds the timeout, return an empty list."""

        async def _slow_parse(*_args, **_kwargs):
            await asyncio.sleep(10)

        source_config["timeout"] = 0.01  # very short timeout

        with patch(
            "src.scrapers.rss_scraper.feedparser.parse",
            side_effect=lambda *a: time.sleep(5),
        ):
            scraper = RssScraper(source_config)
            articles = await scraper.scrape()

        assert articles == []


class TestRssScraperClose:
    """The close method should be a harmless no-op."""

    @pytest.mark.asyncio
    async def test_close_is_noop(self, source_config):
        scraper = RssScraper(source_config)
        await scraper.close()  # should not raise
