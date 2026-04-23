"""Scraper registry — maps scraper_type strings to scraper classes.

Provides factory methods and auto-detection of the best scraper for a URL.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """Maps scraper types to classes and auto-detects best scraper for a URL."""

    _scrapers: dict[str, type[BaseScraper]] = {}

    # Patterns that indicate an RSS/Atom feed URL.
    _RSS_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"/feed/?$", re.IGNORECASE),
        re.compile(r"/rss/?$", re.IGNORECASE),
        re.compile(r"/atom/?$", re.IGNORECASE),
        re.compile(r"\.xml$", re.IGNORECASE),
        re.compile(r"\.rss$", re.IGNORECASE),
        re.compile(r"\.atom$", re.IGNORECASE),
        re.compile(r"/feeds?/", re.IGNORECASE),
        re.compile(r"[?&]format=rss", re.IGNORECASE),
        re.compile(r"[?&]format=atom", re.IGNORECASE),
    ]

    # Domain -> scraper_type mappings for known API sources.
    _API_DOMAINS: dict[str, str] = {
        "github.com": "github",
        "reddit.com": "reddit",
        "www.reddit.com": "reddit",
        "old.reddit.com": "reddit",
        "arxiv.org": "api",
        "news.ycombinator.com": "api",
    }

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    @classmethod
    def register(cls, scraper_type: str, scraper_class: type[BaseScraper]) -> None:
        """Register a scraper class for a given type string."""
        cls._scrapers[scraper_type] = scraper_class
        logger.debug("Registered scraper type '%s' -> %s", scraper_type, scraper_class.__name__)

    # ------------------------------------------------------------------ #
    # Lookup
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls, scraper_type: str, config: dict[str, Any] | None = None) -> BaseScraper:
        """Return a new scraper instance for *scraper_type*.

        Raises ``KeyError`` if the type has not been registered.
        """
        if scraper_type not in cls._scrapers:
            raise KeyError(
                f"Unknown scraper type '{scraper_type}'. "
                f"Registered types: {list(cls._scrapers.keys())}"
            )
        return cls._scrapers[scraper_type](config)

    # ------------------------------------------------------------------ #
    # Auto-detection
    # ------------------------------------------------------------------ #

    @classmethod
    def auto_detect(cls, url: str, config: dict[str, Any] | None = None) -> BaseScraper:
        """Pick the best scraper for a URL and return a new instance.

        Strategy:
        1. Check if URL looks like an RSS/Atom feed.
        2. Check if URL matches a known API-backed domain.
        3. Default to Firecrawl for everything else (fallback to rss if
           firecrawl is not registered).
        """
        # --- 1. RSS detection -----------------------------------------------
        for pattern in cls._RSS_PATTERNS:
            if pattern.search(url):
                if "rss" in cls._scrapers:
                    logger.debug("Auto-detected RSS scraper for %s", url)
                    return cls._scrapers["rss"](config)

        # --- 2. Known API domains -------------------------------------------
        # Extract the domain from the URL (simple, no urllib dependency needed
        # for the patterns we care about).
        try:
            # Handles both "https://example.com/path" and bare "example.com"
            domain = _extract_domain(url)
        except Exception:
            domain = ""

        if domain in cls._API_DOMAINS:
            scraper_type = cls._API_DOMAINS[domain]
            if scraper_type in cls._scrapers:
                logger.debug(
                    "Auto-detected '%s' scraper for domain %s", scraper_type, domain
                )
                return cls._scrapers[scraper_type](config)

        # --- 3. Default: Firecrawl (or first available fallback) ------------
        if "firecrawl" in cls._scrapers:
            logger.debug("Defaulting to Firecrawl scraper for %s", url)
            return cls._scrapers["firecrawl"](config)

        # Firecrawl not registered yet — fall back to rss as a safe default.
        if "rss" in cls._scrapers:
            logger.debug("Firecrawl not available; falling back to RSS for %s", url)
            return cls._scrapers["rss"](config)

        raise RuntimeError(
            "No suitable scraper registered. "
            f"Registered types: {list(cls._scrapers.keys())}"
        )

    @classmethod
    def registered_types(cls) -> list[str]:
        """Return a sorted list of all registered scraper type strings."""
        return sorted(cls._scrapers.keys())


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _extract_domain(url: str) -> str:
    """Return the hostname from a URL string."""
    # Remove scheme
    if "://" in url:
        url = url.split("://", 1)[1]
    # Remove path / query / fragment
    domain = url.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return domain.lower()


# ---------------------------------------------------------------------- #
# Auto-register all known scrapers at import time
# ---------------------------------------------------------------------- #

def _register_all() -> None:
    """Import and register every concrete scraper."""
    from src.scrapers.rss_scraper import RssScraper
    from src.scrapers.github_scraper import GitHubScraper
    from src.scrapers.reddit_scraper import RedditScraper

    ScraperRegistry.register("rss", RssScraper)
    ScraperRegistry.register("github", GitHubScraper)
    ScraperRegistry.register("reddit", RedditScraper)

    # Future scrapers — register as they become available:
    # from src.scrapers.playwright_scraper import PlaywrightScraper
    # ScraperRegistry.register("playwright", PlaywrightScraper)
    #
    # from src.scrapers.firecrawl_scraper import FirecrawlScraper
    # ScraperRegistry.register("firecrawl", FirecrawlScraper)


_register_all()
