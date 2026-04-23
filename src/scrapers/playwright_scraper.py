"""Playwright scraper — renders JavaScript-heavy pages via headless Chromium."""

import logging
from typing import Any

from playwright.async_api import (
    Browser,
    Playwright,
    async_playwright,
)

from src.scrapers.base import BaseScraper, RawArticle

logger = logging.getLogger(__name__)

# Navigation timeout per page (milliseconds).
_PAGE_TIMEOUT_MS = 30_000

# Realistic browser viewport dimensions.
_VIEWPORT_WIDTH = 1920
_VIEWPORT_HEIGHT = 1080

# User-agent string to reduce bot detection.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightScraper(BaseScraper):
    """Scrape JavaScript-rendered pages using headless Chromium.

    Useful for sites that rely on client-side rendering or require
    scrolling to trigger lazy-loaded content.

    Config keys:
        urls: List of URLs to scrape.
        wait_for: Optional CSS selector to wait for before extracting content.
        scroll: If True, scroll to page bottom to trigger lazy loading (default False).
    """

    SOURCE_NAME = "playwright"

    def __init__(self, source_config: dict[str, Any] | None = None) -> None:
        super().__init__(source_config)
        self._urls: list[str] = self.config.get("urls", [])
        self._wait_for: str | None = self.config.get("wait_for")
        self._scroll: bool = self.config.get("scroll", False)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> Browser:
        """Launch a browser instance if one is not already running."""
        if self._browser is None or not self._browser.is_connected():
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            logger.debug("Playwright browser launched")
        return self._browser

    async def _scrape_url(self, url: str) -> RawArticle | None:
        """Navigate to a single URL and extract its content."""
        browser = await self._ensure_browser()
        context = await browser.new_context(
            viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
            user_agent=_USER_AGENT,
        )
        page = await context.new_page()

        try:
            page.set_default_timeout(_PAGE_TIMEOUT_MS)

            await page.goto(url, wait_until="domcontentloaded")

            # Wait for specific selector or fall back to networkidle.
            if self._wait_for:
                try:
                    await page.wait_for_selector(self._wait_for, timeout=_PAGE_TIMEOUT_MS)
                except Exception:
                    logger.warning(
                        "Selector '%s' not found on %s — proceeding anyway",
                        self._wait_for,
                        url,
                    )
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=_PAGE_TIMEOUT_MS)
                except Exception:
                    logger.debug("networkidle timeout on %s — proceeding with current state", url)

            # Scroll to bottom if configured to trigger lazy-loaded content.
            if self._scroll:
                await self._scroll_to_bottom(page)

            html = await page.content()
            title = await page.title()

            return RawArticle(
                title=title or "",
                url=url,
                raw_content=html,
                source_name=self.SOURCE_NAME,
                published_at=None,
                author=None,
                metadata={
                    "scraper": "playwright",
                    "wait_for": self._wait_for,
                    "scrolled": self._scroll,
                },
            )

        except Exception as exc:
            logger.error("Failed to scrape %s via Playwright: %s", url, exc)
            return None

        finally:
            await page.close()
            await context.close()

    @staticmethod
    async def _scroll_to_bottom(page) -> None:
        """Incrementally scroll to the bottom of the page."""
        try:
            await page.evaluate(
                """
                async () => {
                    await new Promise((resolve) => {
                        let totalHeight = 0;
                        const distance = 300;
                        const timer = setInterval(() => {
                            window.scrollBy(0, distance);
                            totalHeight += distance;
                            if (totalHeight >= document.body.scrollHeight) {
                                clearInterval(timer);
                                resolve();
                            }
                        }, 100);
                        // Safety timeout: stop scrolling after 10 seconds.
                        setTimeout(() => { clearInterval(timer); resolve(); }, 10000);
                    });
                }
                """
            )
        except Exception:
            logger.debug("Scroll evaluation failed — page may not support it")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[RawArticle]:
        """Scrape all configured URLs via headless Chromium and return articles."""
        articles: list[RawArticle] = []
        try:
            for url in self._urls:
                article = await self._scrape_url(url)
                if article is not None:
                    articles.append(article)
        except Exception:
            logger.exception("Unexpected error in PlaywrightScraper.scrape")

        logger.info(
            "Playwright scraped %d articles from %d URLs", len(articles), len(self._urls)
        )
        return articles

    async def close(self) -> None:
        """Close the browser and Playwright instance."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Error closing Playwright browser", exc_info=True)
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("Error stopping Playwright", exc_info=True)
            self._playwright = None
