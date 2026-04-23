"""Tests for ScraperRegistry — type lookup and URL auto-detection."""

import pytest

from src.scrapers.base import BaseScraper
from src.scrapers.registry import ScraperRegistry
from src.scrapers.rss_scraper import RssScraper
from src.scrapers.github_scraper import GitHubScraper
from src.scrapers.reddit_scraper import RedditScraper


# ------------------------------------------------------------------ #
# Registration & get()
# ------------------------------------------------------------------ #

class TestScraperRegistryGet:
    """Tests for ScraperRegistry.get()."""

    def test_get_rss_returns_rss_scraper(self) -> None:
        scraper = ScraperRegistry.get("rss", {"feed_url": "https://example.com/feed"})
        assert isinstance(scraper, RssScraper)

    def test_get_github_returns_github_scraper(self) -> None:
        scraper = ScraperRegistry.get("github")
        assert isinstance(scraper, GitHubScraper)

    def test_get_reddit_returns_reddit_scraper(self) -> None:
        scraper = ScraperRegistry.get("reddit")
        assert isinstance(scraper, RedditScraper)

    def test_get_unknown_type_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="Unknown scraper type"):
            ScraperRegistry.get("nonexistent_scraper")

    def test_get_passes_config_to_instance(self) -> None:
        config = {"feed_url": "https://blog.example.com/rss", "timeout": 10}
        scraper = ScraperRegistry.get("rss", config)
        assert scraper.config == config

    def test_get_with_none_config(self) -> None:
        scraper = ScraperRegistry.get("github", None)
        assert isinstance(scraper, GitHubScraper)
        assert scraper.config == {}

    def test_registered_types_includes_all_known(self) -> None:
        types = ScraperRegistry.registered_types()
        assert "rss" in types
        assert "github" in types
        assert "reddit" in types


# ------------------------------------------------------------------ #
# Auto-detect
# ------------------------------------------------------------------ #

class TestScraperRegistryAutoDetect:
    """Tests for ScraperRegistry.auto_detect()."""

    # --- RSS detection --------------------------------------------------

    @pytest.mark.parametrize(
        "url",
        [
            "https://blog.example.com/feed",
            "https://blog.example.com/feed/",
            "https://example.com/rss",
            "https://example.com/atom",
            "https://example.com/news.xml",
            "https://example.com/feed.rss",
            "https://example.com/blog.atom",
            "https://example.com/path/feeds/main",
            "https://example.com/articles?format=rss",
            "https://example.com/articles?format=atom",
        ],
    )
    def test_auto_detect_rss_urls(self, url: str) -> None:
        scraper = ScraperRegistry.auto_detect(url, {"feed_url": url})
        assert isinstance(scraper, RssScraper)

    # --- GitHub detection -----------------------------------------------

    def test_auto_detect_github_url(self) -> None:
        scraper = ScraperRegistry.auto_detect("https://github.com/openai/gpt-4")
        assert isinstance(scraper, GitHubScraper)

    # --- Reddit detection -----------------------------------------------

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.reddit.com/r/MachineLearning",
            "https://reddit.com/r/artificial",
            "https://old.reddit.com/r/technology",
        ],
    )
    def test_auto_detect_reddit_urls(self, url: str) -> None:
        scraper = ScraperRegistry.auto_detect(url)
        assert isinstance(scraper, RedditScraper)

    # --- Default fallback -----------------------------------------------

    def test_auto_detect_unknown_url_falls_back(self) -> None:
        """Unknown URLs should fall back to firecrawl or rss."""
        url = "https://techcrunch.com/some-article"
        scraper = ScraperRegistry.auto_detect(url, {"feed_url": url})
        # Firecrawl is not registered yet, so should fall back to rss
        assert isinstance(scraper, BaseScraper)

    def test_auto_detect_passes_config(self) -> None:
        config = {"feed_url": "https://example.com/feed", "timeout": 5}
        scraper = ScraperRegistry.auto_detect("https://example.com/feed", config)
        assert scraper.config == config

    # --- Custom registration --------------------------------------------

    def test_register_and_get_custom_scraper(self) -> None:
        """Verify that dynamically registered scrapers work."""

        class _DummyScraper(BaseScraper):
            async def scrape(self):
                return []

            async def close(self):
                pass

        ScraperRegistry.register("_test_dummy", _DummyScraper)
        try:
            scraper = ScraperRegistry.get("_test_dummy")
            assert isinstance(scraper, _DummyScraper)
        finally:
            # Clean up to avoid polluting other tests.
            ScraperRegistry._scrapers.pop("_test_dummy", None)
