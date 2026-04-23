"""Unit tests for the Firecrawl scraper."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.scrapers.base import RawArticle
from src.scrapers.firecrawl_scraper import FirecrawlScraper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _success_response(
    title: str = "Test Article",
    markdown: str = "# Hello\n\nArticle body.",
    url: str = "https://example.com/article",
) -> httpx.Response:
    """Build a mock httpx.Response mimicking a successful Firecrawl scrape."""
    response = httpx.Response(
        status_code=200,
        json={
            "success": True,
            "data": {
                "markdown": markdown,
                "title": title,
                "metadata": {
                    "title": title,
                    "description": "A test article.",
                    "language": "en",
                    "sourceURL": url,
                    "author": "Test Author",
                },
            },
        },
        request=httpx.Request("POST", "http://localhost:3002/v1/scrape"),
    )
    return response


def _error_response(status_code: int = 500) -> httpx.Response:
    """Build a mock httpx.Response for a failing Firecrawl request."""
    return httpx.Response(
        status_code=status_code,
        json={"success": False, "error": "Internal server error"},
        request=httpx.Request("POST", "http://localhost:3002/v1/scrape"),
    )


def _api_error_response() -> httpx.Response:
    """Build a 200 response where Firecrawl reports a logical error."""
    return httpx.Response(
        status_code=200,
        json={"success": False, "error": "Failed to scrape URL"},
        request=httpx.Request("POST", "http://localhost:3002/v1/scrape"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def source_config():
    return {"urls": ["https://example.com/article-1", "https://example.com/article-2"]}


@pytest.fixture
def single_url_config():
    return {"urls": ["https://example.com/article-1"]}


# ---------------------------------------------------------------------------
# Tests — successful scraping
# ---------------------------------------------------------------------------

class TestFirecrawlScraperSuccess:
    """Successful scrape scenarios."""

    @pytest.mark.asyncio
    async def test_scrapes_all_urls(self, source_config):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=[
                _success_response(title="Article 1", url="https://example.com/article-1"),
                _success_response(title="Article 2", url="https://example.com/article-2"),
            ]
        )

        scraper = FirecrawlScraper(source_config)
        scraper._client = mock_client

        articles = await scraper.scrape()

        assert len(articles) == 2
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].title == "Article 1"
        assert articles[1].title == "Article 2"

    @pytest.mark.asyncio
    async def test_article_fields(self, single_url_config):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=_success_response())

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        articles = await scraper.scrape()

        assert len(articles) == 1
        article = articles[0]
        assert article.title == "Test Article"
        assert article.url == "https://example.com/article-1"
        assert article.raw_content == "# Hello\n\nArticle body."
        assert article.source_name == "firecrawl"
        assert article.author == "Test Author"
        assert article.metadata["description"] == "A test article."
        assert article.metadata["language"] == "en"
        assert article.metadata["scraper"] == "firecrawl"

    @pytest.mark.asyncio
    async def test_empty_urls_returns_empty(self):
        scraper = FirecrawlScraper({"urls": []})
        articles = await scraper.scrape()
        assert articles == []

    @pytest.mark.asyncio
    async def test_no_config_returns_empty(self):
        scraper = FirecrawlScraper()
        articles = await scraper.scrape()
        assert articles == []


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------

class TestFirecrawlScraperErrors:
    """Error handling and retry behaviour."""

    @pytest.mark.asyncio
    async def test_server_error_retries_and_returns_empty(self, single_url_config):
        """A 500 response triggers one retry; both fail -> empty list."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=_error_response(500))

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        with patch("src.scrapers.firecrawl_scraper.asyncio.sleep", new_callable=AsyncMock):
            articles = await scraper.scrape()

        assert articles == []
        assert mock_client.post.call_count == 2  # initial + 1 retry

    @pytest.mark.asyncio
    async def test_api_error_response_retries(self, single_url_config):
        """A 200 with success=False triggers retry."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=_api_error_response())

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        with patch("src.scrapers.firecrawl_scraper.asyncio.sleep", new_callable=AsyncMock):
            articles = await scraper.scrape()

        assert articles == []
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, single_url_config):
        """First call fails, second succeeds — should return one article."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=[_error_response(500), _success_response()]
        )

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        with patch("src.scrapers.firecrawl_scraper.asyncio.sleep", new_callable=AsyncMock):
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert articles[0].title == "Test Article"

    @pytest.mark.asyncio
    async def test_connection_error_retries(self, single_url_config):
        """ConnectError (Firecrawl down) triggers retry."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        with patch("src.scrapers.firecrawl_scraper.asyncio.sleep", new_callable=AsyncMock):
            articles = await scraper.scrape()

        assert articles == []
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_retries(self, single_url_config):
        """Timeout triggers retry."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=httpx.ReadTimeout("Read timed out")
        )

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        with patch("src.scrapers.firecrawl_scraper.asyncio.sleep", new_callable=AsyncMock):
            articles = await scraper.scrape()

        assert articles == []
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_partial_failure_returns_successful_urls(self, source_config):
        """One URL fails, another succeeds — only the successful one is returned."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),  # retry for first URL
                _success_response(title="Article 2"),
            ]
        )

        scraper = FirecrawlScraper(source_config)
        scraper._client = mock_client

        with patch("src.scrapers.firecrawl_scraper.asyncio.sleep", new_callable=AsyncMock):
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert articles[0].title == "Article 2"


# ---------------------------------------------------------------------------
# Tests — request payload
# ---------------------------------------------------------------------------

class TestFirecrawlScraperRequest:
    """Verify the correct Firecrawl API call is made."""

    @pytest.mark.asyncio
    async def test_posts_correct_payload(self, single_url_config):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=_success_response())

        scraper = FirecrawlScraper(single_url_config)
        scraper._client = mock_client

        await scraper.scrape()

        mock_client.post.assert_called_once_with(
            "http://localhost:3002/v1/scrape",
            json={"url": "https://example.com/article-1", "formats": ["markdown"]},
        )


# ---------------------------------------------------------------------------
# Tests — close
# ---------------------------------------------------------------------------

class TestFirecrawlScraperClose:
    """Resource cleanup."""

    @pytest.mark.asyncio
    async def test_close_closes_client(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        scraper = FirecrawlScraper({"urls": []})
        scraper._client = mock_client

        await scraper.close()

        mock_client.aclose.assert_awaited_once()
        assert scraper._client is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_client(self):
        scraper = FirecrawlScraper({"urls": []})
        await scraper.close()  # should not raise
