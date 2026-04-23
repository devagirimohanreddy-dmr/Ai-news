"""Base scraper interface and shared data classes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawArticle:
    """Normalized article data from any scraper."""

    title: str
    url: str
    raw_content: str
    source_name: str
    published_at: datetime | None = None
    author: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseScraper(ABC):
    """Abstract base for all scraper adapters.

    Each scraper implements `scrape()` to fetch articles from its source
    and return them as a list of `RawArticle` instances.
    """

    def __init__(self, source_config: dict[str, Any] | None = None):
        self.config = source_config or {}

    @abstractmethod
    async def scrape(self) -> list[RawArticle]:
        """Fetch articles from the source.

        Returns a list of RawArticle instances with normalized data.
        Implementations should handle their own error recovery and
        return an empty list on failure rather than raising.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up any resources (browser instances, sessions, etc.)."""
        ...
