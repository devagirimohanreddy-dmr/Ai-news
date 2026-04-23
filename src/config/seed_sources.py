"""
Seed script for the sources table.

Inserts the initial set of news sources used by the scraper scheduler.
Idempotent -- safe to run multiple times; existing sources (matched by name)
are skipped.

Usage:
    python -m src.config.seed_sources
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from src.models.base import get_session_factory
from src.models.source import Source

INITIAL_SOURCES = [
    # -----------------------------------------------------------------------
    # RSS sources
    # -----------------------------------------------------------------------
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "scraper_type": "rss",
        "schedule_cron": "*/30 * * * *",
        "priority": 2,
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "scraper_type": "rss",
        "schedule_cron": "*/30 * * * *",
        "priority": 2,
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "scraper_type": "rss",
        "schedule_cron": "*/60 * * * *",
        "priority": 1,
    },
    {
        "name": "Wired",
        "url": "https://www.wired.com/feed/rss",
        "scraper_type": "rss",
        "schedule_cron": "*/60 * * * *",
        "priority": 1,
    },
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
        "scraper_type": "rss",
        "schedule_cron": "*/60 * * * *",
        "priority": 2,
    },
    {
        "name": "VentureBeat",
        "url": "https://venturebeat.com/feed/",
        "scraper_type": "rss",
        "schedule_cron": "*/30 * * * *",
        "priority": 2,
    },
    # -----------------------------------------------------------------------
    # API sources
    # -----------------------------------------------------------------------
    {
        "name": "GitHub Trending AI",
        "url": "https://github.com/trending",
        "scraper_type": "api",
        "schedule_cron": "*/60 * * * *",
        "priority": 2,
        "config_json": {"type": "github", "trending_language": "python"},
    },
    {
        "name": "Reddit r/MachineLearning",
        "url": "https://reddit.com/r/MachineLearning",
        "scraper_type": "api",
        "schedule_cron": "*/30 * * * *",
        "priority": 2,
        "config_json": {"type": "reddit", "subreddits": ["MachineLearning"]},
    },
    {
        "name": "Reddit r/artificial",
        "url": "https://reddit.com/r/artificial",
        "scraper_type": "api",
        "schedule_cron": "*/30 * * * *",
        "priority": 1,
        "config_json": {"type": "reddit", "subreddits": ["artificial"]},
    },
    {
        "name": "Reddit r/technology",
        "url": "https://reddit.com/r/technology",
        "scraper_type": "api",
        "schedule_cron": "*/60 * * * *",
        "priority": 1,
        "config_json": {"type": "reddit", "subreddits": ["technology"]},
    },
    {
        "name": "Hacker News",
        "url": "https://news.ycombinator.com",
        "scraper_type": "api",
        "schedule_cron": "*/15 * * * *",
        "priority": 2,
        "config_json": {"type": "hn", "story_type": "top", "limit": 30},
    },
    {
        "name": "arXiv AI/ML",
        "url": "https://arxiv.org",
        "scraper_type": "api",
        "schedule_cron": "0 */6 * * *",
        "priority": 2,
        "config_json": {
            "type": "arxiv",
            "categories": ["cs.AI", "cs.LG", "cs.CL"],
        },
    },
    # -----------------------------------------------------------------------
    # Firecrawl sources (web-scraping via Firecrawl service)
    # -----------------------------------------------------------------------
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog",
        "scraper_type": "firecrawl",
        "schedule_cron": "*/60 * * * *",
        "priority": 3,
        "config_json": {"urls": ["https://openai.com/blog"]},
    },
    {
        "name": "Anthropic Blog",
        "url": "https://www.anthropic.com/news",
        "scraper_type": "firecrawl",
        "schedule_cron": "*/60 * * * *",
        "priority": 3,
        "config_json": {"urls": ["https://www.anthropic.com/news"]},
    },
    {
        "name": "Google DeepMind Blog",
        "url": "https://deepmind.google/discover/blog/",
        "scraper_type": "firecrawl",
        "schedule_cron": "*/60 * * * *",
        "priority": 3,
        "config_json": {"urls": ["https://deepmind.google/discover/blog/"]},
    },
    {
        "name": "Meta AI Blog",
        "url": "https://ai.meta.com/blog/",
        "scraper_type": "firecrawl",
        "schedule_cron": "*/60 * * * *",
        "priority": 3,
        "config_json": {"urls": ["https://ai.meta.com/blog/"]},
    },
]


async def seed_sources() -> None:
    """Insert sources into the database, skipping any that already exist."""
    session_factory = get_session_factory()

    async with session_factory() as session:
        async with session.begin():
            for src_data in INITIAL_SOURCES:
                result = await session.execute(
                    select(Source).where(Source.name == src_data["name"])
                )
                existing = result.scalar_one_or_none()

                if existing is not None:
                    print(f"  [EXISTS]  {src_data['name']}")
                else:
                    source = Source(
                        name=src_data["name"],
                        url=src_data["url"],
                        scraper_type=src_data["scraper_type"],
                        schedule_cron=src_data.get("schedule_cron"),
                        priority=src_data.get("priority", 1),
                        config_json=src_data.get("config_json"),
                    )
                    session.add(source)
                    print(f"  [CREATED] {src_data['name']}")

    print(f"\nSource seeding complete. ({len(INITIAL_SOURCES)} sources defined)")


if __name__ == "__main__":
    asyncio.run(seed_sources())
