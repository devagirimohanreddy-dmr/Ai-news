"""Unit tests for the four API scraper adapters.

Tests use unittest.mock to patch httpx calls so no real network requests are made.
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import RawArticle
from src.scrapers.github_scraper import GitHubScraper
from src.scrapers.reddit_scraper import RedditScraper
from src.scrapers.hn_scraper import HackerNewsScraper
from src.scrapers.arxiv_scraper import ArxivScraper


# ======================================================================
# Helpers
# ======================================================================

def _mock_response(
    status_code: int = 200,
    json_data: object = None,
    text: str = "",
    headers: dict | None = None,
) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "9999999999"}
    resp.json.return_value = json_data
    resp.text = text
    return resp


# ======================================================================
# GitHubScraper
# ======================================================================


class TestGitHubScraper:
    """Tests for GitHubScraper."""

    @pytest.fixture
    def scraper(self):
        return GitHubScraper(
            {"github_token": "ghp_test123", "repos": ["huggingface/transformers"]}
        )

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        releases_data = [
            {
                "name": "v4.40.0",
                "tag_name": "v4.40.0",
                "html_url": "https://github.com/huggingface/transformers/releases/tag/v4.40.0",
                "body": "Release notes here",
                "published_at": "2025-04-01T12:00:00Z",
                "author": {"login": "huggingface-bot"},
                "prerelease": False,
            }
        ]
        search_data = {
            "items": [
                {
                    "full_name": "cool/ai-project",
                    "description": "An awesome AI project",
                    "html_url": "https://github.com/cool/ai-project",
                    "created_at": "2025-04-15T10:00:00Z",
                    "owner": {"login": "cool"},
                    "stargazers_count": 500,
                    "language": "Python",
                    "forks_count": 20,
                    "topics": ["ai", "ml"],
                }
            ]
        }

        mock_release_resp = _mock_response(json_data=releases_data)
        mock_search_resp = _mock_response(json_data=search_data)

        async def side_effect(url, **kwargs):
            if "/releases" in url:
                return mock_release_resp
            return mock_search_resp

        with patch("src.scrapers.github_scraper.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=side_effect)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            MockClient.return_value = instance
            scraper._client = instance

            articles = await scraper.scrape()

        assert isinstance(articles, list)
        assert len(articles) == 2
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].source_name == "github"
        assert "Release" in articles[0].title
        assert "Trending" in articles[1].title
        await scraper.close()

    @pytest.mark.asyncio
    async def test_rate_limit_warning(self, scraper, caplog):
        """When X-RateLimit-Remaining is low, a warning is logged."""
        resp = _mock_response(
            json_data=[],
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
        )

        with patch("src.scrapers.github_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            import logging
            with caplog.at_level(logging.WARNING, logger="src.scrapers.github_scraper"):
                await scraper.scrape()

        assert any("rate limit" in r.message.lower() for r in caplog.records)
        await scraper.close()

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self, scraper):
        """On HTTP errors, scraper returns an empty list."""
        import httpx as real_httpx

        with patch("src.scrapers.github_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_no_token_no_auth_header(self):
        scraper = GitHubScraper({"repos": []})
        headers = scraper._build_headers()
        assert "Authorization" not in headers
        await scraper.close()


# ======================================================================
# RedditScraper
# ======================================================================


class TestRedditScraper:
    """Tests for RedditScraper."""

    @pytest.fixture
    def scraper(self):
        return RedditScraper({"subreddits": ["MachineLearning"], "limit": 5})

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        reddit_json = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "GPT-5 released!",
                            "url": "https://openai.com/gpt5",
                            "selftext": "",
                            "author": "ai_fan",
                            "created_utc": 1700000000,
                            "score": 1500,
                            "num_comments": 300,
                            "is_self": False,
                            "permalink": "/r/MachineLearning/comments/abc/gpt5/",
                        }
                    },
                    {
                        "data": {
                            "title": "Low score post",
                            "url": "https://example.com",
                            "selftext": "",
                            "author": "nobody",
                            "created_utc": 1700000000,
                            "score": 5,
                            "num_comments": 1,
                            "is_self": False,
                            "permalink": "/r/MachineLearning/comments/xyz/low/",
                        }
                    },
                ]
            }
        }

        mock_resp = _mock_response(json_data=reddit_json)

        with patch("src.scrapers.reddit_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert isinstance(articles, list)
        # Only the high-score post passes the filter
        assert len(articles) == 1
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].title == "GPT-5 released!"
        assert articles[0].source_name == "reddit"
        await scraper.close()

    @pytest.mark.asyncio
    async def test_rate_limit_backoff(self, scraper):
        """429 response triggers a retry after backoff."""
        rate_limited = _mock_response(status_code=429, headers={"Retry-After": "1"}, json_data={})
        ok_resp = _mock_response(
            json_data={"data": {"children": []}},
        )

        call_count = 0

        async def get_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return rate_limited
            return ok_resp

        with patch("src.scrapers.reddit_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=get_side_effect)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            with patch("src.scrapers.reddit_scraper.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                articles = await scraper.scrape()

            mock_sleep.assert_awaited_once_with(1)
        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self, scraper):
        import httpx as real_httpx

        with patch("src.scrapers.reddit_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()


# ======================================================================
# HackerNewsScraper
# ======================================================================


class TestHackerNewsScraper:
    """Tests for HackerNewsScraper."""

    @pytest.fixture
    def scraper(self):
        return HackerNewsScraper({"story_type": "top", "limit": 3})

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        story_ids = [100, 200, 300]
        stories = {
            100: {
                "id": 100,
                "title": "Show HN: Cool AI Thing",
                "url": "https://example.com/cool",
                "by": "hacker",
                "time": 1700000000,
                "score": 200,
                "descendants": 50,
                "type": "story",
            },
            200: {
                "id": 200,
                "title": "Low score story",
                "url": "https://example.com/low",
                "by": "nobody",
                "time": 1700000000,
                "score": 10,
                "descendants": 2,
                "type": "story",
            },
            300: {
                "id": 300,
                "title": "Another great AI story",
                "url": "https://example.com/great",
                "by": "ai_lover",
                "time": 1700000000,
                "score": 500,
                "descendants": 120,
                "type": "story",
            },
        }

        async def get_side_effect(url, **kwargs):
            if "topstories" in url:
                return _mock_response(json_data=story_ids)
            for sid, story in stories.items():
                if f"/item/{sid}.json" in url:
                    return _mock_response(json_data=story)
            return _mock_response(status_code=404)

        with patch("src.scrapers.hn_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=get_side_effect)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert isinstance(articles, list)
        # Only stories with score > 50 pass
        assert len(articles) == 2
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].source_name == "hackernews"
        assert articles[0].metadata["score"] == 200
        await scraper.close()

    @pytest.mark.asyncio
    async def test_empty_story_ids_returns_empty(self, scraper):
        with patch("src.scrapers.hn_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_response(json_data=[]))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_error_returns_empty_list(self, scraper):
        import httpx as real_httpx

        with patch("src.scrapers.hn_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_invalid_story_type_falls_back(self):
        scraper = HackerNewsScraper({"story_type": "invalid"})
        assert scraper._story_type == "top"
        await scraper.close()


# ======================================================================
# ArxivScraper
# ======================================================================


ARXIV_SAMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>Attention Is All You Need (Again)</title>
    <summary>We propose a new transformer variant that improves on prior work.</summary>
    <published>2025-04-10T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <link href="http://arxiv.org/abs/2504.00001" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2504.00001" title="pdf" type="application/pdf"/>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
    <arxiv:primary_category term="cs.AI"/>
  </entry>
</feed>
"""


class TestArxivScraper:
    """Tests for ArxivScraper."""

    @pytest.fixture
    def scraper(self):
        return ArxivScraper({"categories": ["cs.AI"], "max_results": 10})

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        mock_resp = _mock_response(text=ARXIV_SAMPLE_XML)

        with patch("src.scrapers.arxiv_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert isinstance(articles, list)
        assert len(articles) == 1
        assert all(isinstance(a, RawArticle) for a in articles)

        art = articles[0]
        assert art.source_name == "arxiv"
        assert "Attention" in art.title
        assert art.author == "Alice Smith, Bob Jones"
        assert art.metadata["pdf_url"] == "http://arxiv.org/pdf/2504.00001"
        assert "cs.AI" in art.metadata["categories"]
        assert art.published_at is not None
        await scraper.close()

    @pytest.mark.asyncio
    async def test_malformed_xml_returns_empty(self, scraper):
        mock_resp = _mock_response(text="<not valid xml!!!")

        with patch("src.scrapers.arxiv_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_list(self, scraper):
        import httpx as real_httpx

        with patch("src.scrapers.arxiv_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_empty_feed_returns_empty(self, scraper):
        empty_xml = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        mock_resp = _mock_response(text=empty_xml)

        with patch("src.scrapers.arxiv_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_non_200_returns_empty(self, scraper):
        mock_resp = _mock_response(status_code=503, text="Service Unavailable")

        with patch("src.scrapers.arxiv_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()
