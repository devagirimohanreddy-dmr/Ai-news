"""Backfill image_url for existing articles by re-scraping RSS feeds."""

import asyncio
import re
import hashlib
import feedparser
from datetime import datetime, timezone

from sqlalchemy import select, update

from src.models.base import get_session_factory
from src.models.article import Article


def extract_image_from_feed_entry(entry):
    """Extract image URL from a feed entry."""
    # media:thumbnail
    thumbs = getattr(entry, "media_thumbnail", None)
    if thumbs:
        for t in thumbs:
            url = t.get("url", "")
            if url:
                return url

    # media:content
    media = getattr(entry, "media_content", None)
    if media:
        for m in media:
            if m.get("medium") == "image" or (m.get("type", "").startswith("image/")):
                return m.get("url", "")
            url = m.get("url", "")
            if url and any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                return url

    # Enclosures
    for enc in getattr(entry, "enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href", "") or enc.get("url", "")

    # <img> in content
    content = ""
    if hasattr(entry, "content") and entry.content:
        try:
            content = entry.content[0].get("value", "")
        except Exception:
            pass
    if not content:
        content = getattr(entry, "summary", "") or getattr(entry, "description", "")

    if content:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content)
        if img_match:
            return img_match.group(1)

    return None


async def main():
    feeds = [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/index",
    ]

    # Build URL -> image_url mapping from feeds
    url_to_image = {}
    for feed_url in feeds:
        print(f"Parsing {feed_url}...")
        feed = feedparser.parse(feed_url)
        for entry in feed.get("entries", []):
            link = getattr(entry, "link", "")
            if link:
                img = extract_image_from_feed_entry(entry)
                if img:
                    url_to_image[link] = img

    print(f"\nFound images for {len(url_to_image)} URLs")

    # Update database
    session_factory = get_session_factory()
    updated = 0
    async with session_factory() as session:
        result = await session.execute(
            select(Article).where(Article.image_url.is_(None))
        )
        articles = result.scalars().all()

        for article in articles:
            if article.url in url_to_image:
                article.image_url = url_to_image[article.url]
                updated += 1
                print(f"  [SET] {article.title[:50]}... -> {url_to_image[article.url][:80]}")

        await session.commit()

    print(f"\nUpdated {updated} articles with images.")


if __name__ == "__main__":
    asyncio.run(main())
