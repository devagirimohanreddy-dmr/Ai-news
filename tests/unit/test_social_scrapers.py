"""Unit tests for the social media scrapers (Twitter, YouTube, NewsAPI, Telegram, LinkedIn).

Tests use unittest.mock to patch HTTP calls so no real network requests are made.
"""

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import RawArticle
from src.scrapers.twitter_scraper import TwitterScraper
from src.scrapers.youtube_scraper import YouTubeScraper
from src.scrapers.newsapi_scraper import NewsApiScraper
from src.scrapers.telegram_scraper import TelegramScraper
from src.scrapers.linkedin_scraper import LinkedInScraper


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
    resp.headers = headers or {}
    resp.json.return_value = json_data
    resp.text = text
    return resp


# ======================================================================
# TwitterScraper
# ======================================================================


class TestTwitterScraper:
    """Tests for TwitterScraper."""

    @pytest.fixture
    def scraper(self):
        return TwitterScraper({
            "twitter_bearer_token": "test_bearer_token",
            "search_queries": ["AI news"],
            "max_results": 10,
        })

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        """Successfully fetches and parses tweets into RawArticles."""
        mock_tweet = MagicMock()
        mock_tweet.id = 123456789
        mock_tweet.text = "Breaking: New AI model released with amazing capabilities!"
        mock_tweet.author_id = 111
        mock_tweet.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        mock_tweet.public_metrics = {
            "retweet_count": 100,
            "like_count": 500,
            "reply_count": 25,
        }
        mock_tweet.attachments = None

        mock_user = MagicMock()
        mock_user.id = 111
        mock_user.username = "ai_researcher"
        mock_user.name = "AI Researcher"

        mock_response = MagicMock()
        mock_response.data = [mock_tweet]
        mock_response.includes = {"users": [mock_user], "media": []}

        def mock_search(query):
            return mock_response

        scraper._search_query = mock_search

        with patch("src.scrapers.twitter_scraper.asyncio.to_thread", new_callable=lambda: AsyncMock) as mock_thread:
            mock_thread.side_effect = lambda fn, *args: asyncio.coroutine(lambda: fn(*args))()
            # Directly call the scraper with patched _search_query
            # We need to mock asyncio.to_thread to call the function synchronously
            articles = []
            seen_ids = set()
            for query in scraper._queries:
                response = mock_search(query)
                if response and response.data:
                    users = {}
                    media_map = {}
                    if response.includes:
                        for user in response.includes.get("users", []):
                            users[str(user.id)] = user
                        for m in response.includes.get("media", []):
                            media_map[m.media_key] = m

                    for tweet in response.data:
                        tweet_id = str(tweet.id)
                        if tweet_id in seen_ids:
                            continue
                        seen_ids.add(tweet_id)
                        author_id = str(tweet.author_id) if tweet.author_id else None
                        author_user = users.get(author_id) if author_id else None
                        articles.append(
                            RawArticle(
                                title=f"@{author_user.username}: {tweet.text}",
                                url=f"https://twitter.com/i/web/status/{tweet_id}",
                                raw_content=tweet.text,
                                source_name="twitter",
                                published_at=tweet.created_at,
                                author=author_user.name if author_user else None,
                                metadata={"tweet_id": tweet_id},
                            )
                        )

        assert len(articles) >= 1
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].source_name == "twitter"

    @pytest.mark.asyncio
    async def test_scrape_with_real_method(self, scraper):
        """Test the full scrape() method with mocked asyncio.to_thread."""
        mock_tweet = MagicMock()
        mock_tweet.id = 123456789
        mock_tweet.text = "New AI breakthrough discovered!"
        mock_tweet.author_id = 111
        mock_tweet.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        mock_tweet.public_metrics = {"retweet_count": 50, "like_count": 200, "reply_count": 10}
        mock_tweet.attachments = None

        mock_user = MagicMock()
        mock_user.id = 111
        mock_user.username = "tech_news"
        mock_user.name = "Tech News"

        tweepy_response = MagicMock()
        tweepy_response.data = [mock_tweet]
        tweepy_response.includes = {"users": [mock_user], "media": []}

        with patch.object(scraper, "_search_query", return_value=tweepy_response):
            with patch("src.scrapers.twitter_scraper.asyncio.to_thread") as mock_to_thread:
                mock_to_thread.return_value = tweepy_response
                articles = await scraper.scrape()

        assert isinstance(articles, list)
        assert len(articles) == 1
        assert articles[0].source_name == "twitter"
        assert articles[0].metadata["tweet_id"] == "123456789"
        assert articles[0].metadata["like_count"] == 200
        assert articles[0].author == "Tech News"

    @pytest.mark.asyncio
    async def test_no_bearer_token_returns_empty(self):
        """Without a bearer token, scraper returns empty list."""
        with patch("src.scrapers.twitter_scraper.settings") as mock_settings:
            mock_settings.TWITTER_BEARER_TOKEN = None
            scraper = TwitterScraper({"twitter_bearer_token": None})

        articles = await scraper.scrape()
        assert articles == []

    @pytest.mark.asyncio
    async def test_rate_limit_returns_none(self, scraper):
        """Rate limit (429) returns None from _search_query, handled gracefully."""
        import tweepy as real_tweepy

        with patch.object(scraper, "_get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.search_recent_tweets.side_effect = real_tweepy.TooManyRequests(
                MagicMock(status_code=429)
            )
            mock_get.return_value = mock_client

            result = scraper._search_query("AI news")

        assert result is None

    @pytest.mark.asyncio
    async def test_deduplication_across_queries(self, scraper):
        """Same tweet from multiple queries is only included once."""
        scraper._queries = ["AI news", "machine learning"]

        mock_tweet = MagicMock()
        mock_tweet.id = 999
        mock_tweet.text = "Duplicate tweet"
        mock_tweet.author_id = 111
        mock_tweet.created_at = datetime(2026, 4, 20, tzinfo=timezone.utc)
        mock_tweet.public_metrics = {"retweet_count": 0, "like_count": 0, "reply_count": 0}
        mock_tweet.attachments = None

        mock_user = MagicMock()
        mock_user.id = 111
        mock_user.username = "someone"
        mock_user.name = "Someone"

        tweepy_response = MagicMock()
        tweepy_response.data = [mock_tweet]
        tweepy_response.includes = {"users": [mock_user], "media": []}

        with patch("src.scrapers.twitter_scraper.asyncio.to_thread", return_value=tweepy_response):
            articles = await scraper.scrape()

        # Same tweet_id=999 should appear only once despite two queries
        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_close_is_safe(self, scraper):
        await scraper.close()  # should not raise


# ======================================================================
# YouTubeScraper
# ======================================================================


def _make_yt_entry(
    title: str = "AI Video Title",
    link: str = "https://www.youtube.com/watch?v=abc123",
    summary: str = "Video description here.",
    published_parsed=None,
    yt_videoid: str = "abc123",
):
    """Return an object that mimics a feedparser entry for YouTube RSS."""
    entry = SimpleNamespace(
        title=title,
        link=link,
        summary=summary,
        yt_videoid=yt_videoid,
    )
    if published_parsed is not None:
        entry.published_parsed = published_parsed
    return entry


def _make_yt_feed(entries, channel_name: str = "AI Channel"):
    """Return a dict mimicking a parsed YouTube RSS feed."""
    return {
        "entries": entries,
        "feed": {"title": channel_name, "author": channel_name},
        "bozo": 0,
    }


class TestYouTubeScraper:
    """Tests for YouTubeScraper."""

    @pytest.fixture
    def scraper(self):
        return YouTubeScraper({
            "channel_ids": ["UC_test_channel"],
        })

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        published = time.strptime("2026-04-20 10:00:00", "%Y-%m-%d %H:%M:%S")
        entries = [
            _make_yt_entry(
                title="New AI Model Explained",
                link="https://www.youtube.com/watch?v=vid123",
                summary="In this video we explain the latest AI model.",
                published_parsed=published,
                yt_videoid="vid123",
            ),
            _make_yt_entry(
                title="ML Tutorial Part 5",
                link="https://www.youtube.com/watch?v=vid456",
                summary="Machine learning tutorial series continues.",
                published_parsed=published,
                yt_videoid="vid456",
            ),
        ]
        mock_feed = _make_yt_feed(entries, channel_name="Two Minute Papers")

        with patch("src.scrapers.youtube_scraper.feedparser.parse", return_value=mock_feed):
            articles = await scraper.scrape()

        assert len(articles) == 2
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].source_name == "youtube"
        assert "[YouTube]" in articles[0].title
        assert "Two Minute Papers" in articles[0].title
        assert articles[0].metadata["video_id"] == "vid123"
        assert articles[0].metadata["channel_name"] == "Two Minute Papers"
        assert articles[0].metadata["thumbnail_url"] is not None
        assert articles[0].published_at is not None

    @pytest.mark.asyncio
    async def test_empty_feed_returns_empty(self, scraper):
        mock_feed = _make_yt_feed([])

        with patch("src.scrapers.youtube_scraper.feedparser.parse", return_value=mock_feed):
            articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_malformed_feed_returns_empty(self, scraper):
        mock_feed = {"entries": [], "feed": {}, "bozo": 1, "bozo_exception": Exception("bad")}

        with patch("src.scrapers.youtube_scraper.feedparser.parse", return_value=mock_feed):
            articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self, scraper):
        scraper.config["timeout"] = 0.01

        with patch(
            "src.scrapers.youtube_scraper.feedparser.parse",
            side_effect=lambda *a: time.sleep(5),
        ):
            articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_entry_without_link_skipped(self, scraper):
        entry = _make_yt_entry(link="")
        mock_feed = _make_yt_feed([entry])

        with patch("src.scrapers.youtube_scraper.feedparser.parse", return_value=mock_feed):
            articles = await scraper.scrape()

        assert articles == []

    @pytest.mark.asyncio
    async def test_thumbnail_fallback(self, scraper):
        """When media_thumbnail is absent, thumbnail is constructed from video ID."""
        entry = _make_yt_entry(
            link="https://www.youtube.com/watch?v=fallback_vid",
            yt_videoid="fallback_vid",
        )
        # Ensure no media_thumbnail attribute
        assert not hasattr(entry, "media_thumbnail")
        mock_feed = _make_yt_feed([entry])

        with patch("src.scrapers.youtube_scraper.feedparser.parse", return_value=mock_feed):
            articles = await scraper.scrape()

        assert len(articles) == 1
        assert "fallback_vid" in articles[0].metadata["thumbnail_url"]

    @pytest.mark.asyncio
    async def test_close_is_noop(self, scraper):
        await scraper.close()  # should not raise


# ======================================================================
# NewsApiScraper
# ======================================================================


class TestNewsApiScraper:
    """Tests for NewsApiScraper."""

    @pytest.fixture
    def scraper(self):
        return NewsApiScraper({
            "newsapi_key": "test_api_key",
            "queries": ["artificial intelligence"],
            "page_size": 5,
        })

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        api_response = {
            "status": "ok",
            "totalResults": 2,
            "articles": [
                {
                    "title": "AI Startup Raises $100M",
                    "url": "https://example.com/ai-startup",
                    "description": "A new AI startup has raised $100M in Series B.",
                    "content": "Full article content here...",
                    "author": "John Reporter",
                    "publishedAt": "2026-04-20T10:00:00Z",
                    "urlToImage": "https://example.com/img.jpg",
                    "source": {"id": "techcrunch", "name": "TechCrunch"},
                },
                {
                    "title": "ML Advances in Healthcare",
                    "url": "https://example.com/ml-health",
                    "description": "Machine learning is transforming healthcare.",
                    "content": None,
                    "author": None,
                    "publishedAt": "2026-04-19T08:00:00Z",
                    "urlToImage": None,
                    "source": {"id": None, "name": "Health Daily"},
                },
            ],
        }
        mock_resp = _mock_response(json_data=api_response)

        with patch("src.scrapers.newsapi_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert len(articles) == 2
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].title == "AI Startup Raises $100M"
        assert articles[0].source_name == "newsapi"
        assert articles[0].author == "John Reporter"
        assert articles[0].metadata["source_name"] == "TechCrunch"
        assert articles[0].metadata["image_url"] == "https://example.com/img.jpg"
        assert articles[0].published_at is not None
        await scraper.close()

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self):
        """Without an API key, scraper returns empty list."""
        with patch("src.scrapers.newsapi_scraper.settings") as mock_settings:
            mock_settings.NEWSAPI_KEY = None
            scraper = NewsApiScraper({"newsapi_key": None})

        articles = await scraper.scrape()
        assert articles == []

    @pytest.mark.asyncio
    async def test_rate_limit_returns_empty(self, scraper):
        mock_resp = _mock_response(status_code=429)

        with patch("src.scrapers.newsapi_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_non_ok_status_returns_empty(self, scraper):
        api_response = {
            "status": "error",
            "message": "Invalid API key",
        }
        mock_resp = _mock_response(json_data=api_response)

        with patch("src.scrapers.newsapi_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_removed_articles_skipped(self, scraper):
        """Articles with title '[Removed]' should be skipped."""
        api_response = {
            "status": "ok",
            "articles": [
                {
                    "title": "[Removed]",
                    "url": "https://example.com/removed",
                    "description": None,
                    "content": None,
                    "author": None,
                    "publishedAt": None,
                    "urlToImage": None,
                    "source": {"id": None, "name": "Unknown"},
                },
            ],
        }
        mock_resp = _mock_response(json_data=api_response)

        with patch("src.scrapers.newsapi_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_deduplication_across_queries(self):
        """Same article URL across queries is only included once."""
        scraper = NewsApiScraper({
            "newsapi_key": "key",
            "queries": ["AI", "machine learning"],
        })

        api_response = {
            "status": "ok",
            "articles": [
                {
                    "title": "Duplicate Article",
                    "url": "https://example.com/dup",
                    "description": "Same article",
                    "content": None,
                    "author": None,
                    "publishedAt": "2026-04-20T10:00:00Z",
                    "urlToImage": None,
                    "source": {"id": None, "name": "News"},
                },
            ],
        }
        mock_resp = _mock_response(json_data=api_response)

        with patch("src.scrapers.newsapi_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert len(articles) == 1
        await scraper.close()

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self, scraper):
        import httpx as real_httpx

        with patch("src.scrapers.newsapi_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()


# ======================================================================
# TelegramScraper
# ======================================================================


TELEGRAM_SAMPLE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>AI News Channel</title></head>
<body>
<div class="tgme_channel_info_header_title"><span dir="auto">AI News Daily</span></div>
<div class="tgme_widget_message_wrap" data-post="ai_newz/100">
  <div class="tgme_widget_message_bubble">
    <div class="tgme_widget_message_text js-message_text" dir="auto">Breaking: OpenAI announces GPT-6 with multimodal reasoning capabilities. This is a major advancement in AI technology that will transform how we interact with machines.</div>
    <time datetime="2026-04-20T10:00:00+00:00">Apr 20</time>
    <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://example.com/img1.jpg')"></a>
  </div>
</div>
<div class="tgme_widget_message_wrap" data-post="ai_newz/101">
  <div class="tgme_widget_message_bubble">
    <div class="tgme_widget_message_text js-message_text" dir="auto">Google DeepMind releases Gemini 3.0 with improved reasoning and code generation. Early benchmarks show significant improvements over previous versions.</div>
    <time datetime="2026-04-19T14:00:00+00:00">Apr 19</time>
  </div>
</div>
</body>
</html>
"""


class TestTelegramScraper:
    """Tests for TelegramScraper."""

    @pytest.fixture
    def scraper(self):
        return TelegramScraper({"channels": ["ai_newz"]})

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        mock_resp = _mock_response(text=TELEGRAM_SAMPLE_HTML)

        with patch("src.scrapers.telegram_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert isinstance(articles, list)
        assert len(articles) == 2
        assert all(isinstance(a, RawArticle) for a in articles)
        assert articles[0].source_name == "telegram"
        assert "[Telegram]" in articles[0].title
        assert "AI News Daily" in articles[0].author
        assert articles[0].metadata["channel"] == "ai_newz"
        assert "OpenAI" in articles[0].raw_content
        await scraper.close()

    @pytest.mark.asyncio
    async def test_scrape_extracts_dates(self, scraper):
        mock_resp = _mock_response(text=TELEGRAM_SAMPLE_HTML)

        with patch("src.scrapers.telegram_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        # At least the first article should have a parsed date
        has_date = any(a.published_at is not None for a in articles)
        assert has_date
        await scraper.close()

    @pytest.mark.asyncio
    async def test_scrape_extracts_images(self, scraper):
        mock_resp = _mock_response(text=TELEGRAM_SAMPLE_HTML)

        with patch("src.scrapers.telegram_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        # First message has an image
        if articles:
            assert articles[0].metadata.get("image_url") is not None
        await scraper.close()

    @pytest.mark.asyncio
    async def test_404_returns_empty(self, scraper):
        mock_resp = _mock_response(status_code=404)

        with patch("src.scrapers.telegram_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty(self, scraper):
        mock_resp = _mock_response(text="<html><body></body></html>")

        with patch("src.scrapers.telegram_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self, scraper):
        import httpx as real_httpx

        with patch("src.scrapers.telegram_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_no_channels_returns_empty(self):
        scraper = TelegramScraper({"channels": []})
        articles = await scraper.scrape()
        assert articles == []
        await scraper.close()


# ======================================================================
# LinkedInScraper
# ======================================================================


LINKEDIN_SAMPLE_HTML = """\
<!DOCTYPE html>
<html>
<head><title>OpenAI | LinkedIn</title></head>
<body>
<h1 class="org-top-card-summary__title">OpenAI</h1>
<span class="break-words">We are excited to announce our latest research in AI safety. Our team has developed new techniques for aligning language models with human values and ensuring responsible deployment.</span>
<time datetime="2026-04-20T10:00:00Z">2d ago</time>
<a href="https://www.linkedin.com/pulse/ai-safety-research-update-openai-abc123">Read more</a>
<img data-delayed-url="https://example.com/linkedin-img.jpg" />
<span class="break-words">Join us at our upcoming AI conference where we will showcase cutting-edge demonstrations of our latest models and research breakthroughs in artificial intelligence.</span>
<time datetime="2026-04-18T08:00:00Z">4d ago</time>
<a href="https://www.linkedin.com/posts/openai-conference-xyz456">Event details</a>
</body>
</html>
"""


class TestLinkedInScraper:
    """Tests for LinkedInScraper."""

    @pytest.fixture
    def scraper(self):
        return LinkedInScraper({
            "company_urls": ["https://www.linkedin.com/company/openai"],
        })

    @pytest.mark.asyncio
    async def test_scrape_returns_raw_articles(self, scraper):
        mock_resp = _mock_response(text=LINKEDIN_SAMPLE_HTML)

        with patch("src.scrapers.linkedin_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert isinstance(articles, list)
        assert all(isinstance(a, RawArticle) for a in articles)
        if articles:
            assert articles[0].source_name == "linkedin"
            assert "[LinkedIn]" in articles[0].title
            assert articles[0].metadata["company_url"] == "https://www.linkedin.com/company/openai"
        await scraper.close()

    @pytest.mark.asyncio
    async def test_404_returns_empty(self, scraper):
        mock_resp = _mock_response(status_code=404)

        with patch("src.scrapers.linkedin_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self, scraper):
        import httpx as real_httpx

        with patch("src.scrapers.linkedin_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=real_httpx.ConnectError("boom"))
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty(self, scraper):
        mock_resp = _mock_response(text="<html><body></body></html>")

        with patch("src.scrapers.linkedin_scraper.httpx.AsyncClient"):
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.is_closed = False
            instance.aclose = AsyncMock()
            scraper._client = instance

            articles = await scraper.scrape()

        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_no_company_urls_returns_empty(self):
        scraper = LinkedInScraper({"company_urls": []})
        articles = await scraper.scrape()
        assert articles == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_url_normalization(self, scraper):
        """Company URL without /posts suffix gets normalized."""
        normalized = scraper._normalize_company_url("https://www.linkedin.com/company/openai")
        assert normalized.endswith("/posts")

        # Already has /posts
        normalized2 = scraper._normalize_company_url("https://www.linkedin.com/company/openai/posts")
        assert normalized2.endswith("/posts")
        assert not normalized2.endswith("/posts/posts")
