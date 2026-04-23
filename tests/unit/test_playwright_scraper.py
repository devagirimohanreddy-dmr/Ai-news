"""Unit tests for the Playwright scraper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import RawArticle
from src.scrapers.playwright_scraper import PlaywrightScraper


# ---------------------------------------------------------------------------
# Helpers — mock Playwright objects
# ---------------------------------------------------------------------------

def _make_mock_page(
    title: str = "Test Page",
    html: str = "<html><body><h1>Hello</h1></body></html>",
):
    """Create a mock Playwright page with configurable title and content."""
    page = AsyncMock()
    page.title = AsyncMock(return_value=title)
    page.content = AsyncMock(return_value=html)
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.set_default_timeout = MagicMock()
    page.evaluate = AsyncMock()
    page.close = AsyncMock()
    return page


def _make_mock_context(page):
    """Create a mock browser context that yields the given page."""
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    return context


def _make_mock_browser(context):
    """Create a mock browser that yields the given context."""
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.is_connected = MagicMock(return_value=True)
    browser.close = AsyncMock()
    return browser


def _make_mock_playwright(browser):
    """Create a mock Playwright instance that launches the given browser."""
    pw = AsyncMock()
    pw.chromium = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()
    return pw


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def source_config():
    return {"urls": ["https://example.com/page-1", "https://example.com/page-2"]}


@pytest.fixture
def single_url_config():
    return {"urls": ["https://example.com/page-1"]}


@pytest.fixture
def mock_playwright_stack():
    """Build a full mock stack: playwright -> browser -> context -> page."""
    page = _make_mock_page()
    context = _make_mock_context(page)
    browser = _make_mock_browser(context)
    pw = _make_mock_playwright(browser)
    return pw, browser, context, page


# ---------------------------------------------------------------------------
# Tests — successful scraping
# ---------------------------------------------------------------------------

class TestPlaywrightScraperSuccess:
    """Successful scrape scenarios."""

    @pytest.mark.asyncio
    async def test_scrapes_single_url(self, single_url_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack

        scraper = PlaywrightScraper(single_url_config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert len(articles) == 1
        article = articles[0]
        assert isinstance(article, RawArticle)
        assert article.title == "Test Page"
        assert article.url == "https://example.com/page-1"
        assert article.raw_content == "<html><body><h1>Hello</h1></body></html>"
        assert article.source_name == "playwright"

    @pytest.mark.asyncio
    async def test_scrapes_multiple_urls(self, source_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack
        # Return different titles for the two pages
        page.title = AsyncMock(side_effect=["Page One", "Page Two"])
        page.content = AsyncMock(side_effect=["<html>1</html>", "<html>2</html>"])

        scraper = PlaywrightScraper(source_config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert len(articles) == 2
        assert articles[0].title == "Page One"
        assert articles[1].title == "Page Two"

    @pytest.mark.asyncio
    async def test_empty_urls_returns_empty(self):
        scraper = PlaywrightScraper({"urls": []})
        articles = await scraper.scrape()
        assert articles == []

    @pytest.mark.asyncio
    async def test_no_config_returns_empty(self):
        scraper = PlaywrightScraper()
        articles = await scraper.scrape()
        assert articles == []

    @pytest.mark.asyncio
    async def test_article_metadata(self, single_url_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack

        scraper = PlaywrightScraper(single_url_config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert articles[0].metadata["scraper"] == "playwright"
        assert articles[0].metadata["wait_for"] is None
        assert articles[0].metadata["scrolled"] is False


# ---------------------------------------------------------------------------
# Tests — wait_for selector
# ---------------------------------------------------------------------------

class TestPlaywrightScraperWaitFor:
    """CSS selector wait behaviour."""

    @pytest.mark.asyncio
    async def test_waits_for_selector(self, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack
        config = {"urls": ["https://example.com/spa"], "wait_for": ".article-content"}

        scraper = PlaywrightScraper(config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        page.wait_for_selector.assert_awaited()
        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_selector_timeout_still_returns_content(self, mock_playwright_stack):
        """If the selector is not found, we still extract whatever is on the page."""
        pw, browser, context, page = mock_playwright_stack
        page.wait_for_selector = AsyncMock(side_effect=Exception("Timeout"))
        config = {"urls": ["https://example.com/spa"], "wait_for": ".missing"}

        scraper = PlaywrightScraper(config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert len(articles) == 1


# ---------------------------------------------------------------------------
# Tests — scroll behaviour
# ---------------------------------------------------------------------------

class TestPlaywrightScraperScroll:
    """Scroll-to-bottom behaviour for lazy-loaded content."""

    @pytest.mark.asyncio
    async def test_scrolls_when_configured(self, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack
        config = {"urls": ["https://example.com/feed"], "scroll": True}

        scraper = PlaywrightScraper(config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        page.evaluate.assert_awaited()
        assert len(articles) == 1
        assert articles[0].metadata["scrolled"] is True

    @pytest.mark.asyncio
    async def test_does_not_scroll_by_default(self, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack
        config = {"urls": ["https://example.com/feed"]}

        scraper = PlaywrightScraper(config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        page.evaluate.assert_not_awaited()
        assert articles[0].metadata["scrolled"] is False


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------

class TestPlaywrightScraperErrors:
    """Navigation and page errors."""

    @pytest.mark.asyncio
    async def test_navigation_error_returns_empty(self, single_url_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack
        page.goto = AsyncMock(side_effect=Exception("net::ERR_NAME_NOT_RESOLVED"))

        scraper = PlaywrightScraper(single_url_config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_partial_failure(self, source_config, mock_playwright_stack):
        """First URL fails, second succeeds — only successful one returned."""
        pw, browser, context, page = mock_playwright_stack
        call_count = 0

        async def _mock_goto(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Timeout exceeded")

        page.goto = AsyncMock(side_effect=_mock_goto)

        scraper = PlaywrightScraper(source_config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_content_extraction_error(self, single_url_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack
        page.content = AsyncMock(side_effect=Exception("Page crashed"))

        scraper = PlaywrightScraper(single_url_config)
        scraper._playwright = pw
        scraper._browser = browser

        articles = await scraper.scrape()

        assert articles == []


# ---------------------------------------------------------------------------
# Tests — browser reuse
# ---------------------------------------------------------------------------

class TestPlaywrightScraperBrowserReuse:
    """The scraper should reuse a single browser instance."""

    @pytest.mark.asyncio
    async def test_reuses_existing_browser(self, source_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack

        scraper = PlaywrightScraper(source_config)
        scraper._playwright = pw
        scraper._browser = browser

        await scraper.scrape()

        # Browser was already set, so chromium.launch should NOT be called.
        pw.chromium.launch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_launches_browser_if_none(self, single_url_config, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack

        scraper = PlaywrightScraper(single_url_config)
        # Don't pre-set browser — let _ensure_browser create it.

        with patch(
            "src.scrapers.playwright_scraper.async_playwright"
        ) as mock_async_pw:
            mock_pw_cm = AsyncMock()
            mock_pw_cm.start = AsyncMock(return_value=pw)
            mock_async_pw.return_value = mock_pw_cm

            articles = await scraper.scrape()

        pw.chromium.launch.assert_awaited_once()
        assert len(articles) == 1


# ---------------------------------------------------------------------------
# Tests — close
# ---------------------------------------------------------------------------

class TestPlaywrightScraperClose:
    """Resource cleanup."""

    @pytest.mark.asyncio
    async def test_close_stops_browser_and_playwright(self, mock_playwright_stack):
        pw, browser, context, page = mock_playwright_stack

        scraper = PlaywrightScraper({"urls": []})
        scraper._playwright = pw
        scraper._browser = browser

        await scraper.close()

        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert scraper._browser is None
        assert scraper._playwright is None

    @pytest.mark.asyncio
    async def test_close_noop_when_not_started(self):
        scraper = PlaywrightScraper({"urls": []})
        await scraper.close()  # should not raise
