"""Backfill image_url for existing articles by fetching og:image / twitter:image
directly from each article's URL.

Usage:
    python -m scripts.backfill_images               # backfill all articles missing images
    python -m scripts.backfill_images --limit 100   # limit to 100 articles
    python -m scripts.backfill_images --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from sqlalchemy import select

from src.models.article import Article
from src.models.base import get_session_factory
from src.scrapers.image_extractor import fetch_article_image

logger = logging.getLogger(__name__)


async def _process_one(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    article: Article,
) -> tuple[int, str | None]:
    async with sem:
        image_url = await fetch_article_image(article.url, client=client)
        return article.id, image_url


async def main(limit: int | None, concurrency: int) -> None:
    session_factory = get_session_factory()

    async with session_factory() as session:
        stmt = select(Article).where(Article.image_url.is_(None))
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        articles = result.scalars().all()

    total = len(articles)
    print(f"Found {total} articles without images.")
    if not articles:
        return

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        },
    ) as client:
        tasks = [_process_one(sem, client, a) for a in articles]
        results: list[tuple[int, str | None]] = []
        for i, coro in enumerate(asyncio.as_completed(tasks), start=1):
            article_id, image_url = await coro
            results.append((article_id, image_url))
            if i % 20 == 0 or i == total:
                found = sum(1 for _, u in results if u)
                print(f"  Progress: {i}/{total} processed, {found} images found.")

    # Persist results
    updates = [(aid, url) for aid, url in results if url]
    print(f"\nWriting {len(updates)} image URLs to the database...")

    async with session_factory() as session:
        for article_id, image_url in updates:
            stmt = select(Article).where(Article.id == article_id)
            res = await session.execute(stmt)
            article = res.scalar_one_or_none()
            if article is not None:
                article.image_url = image_url
        await session.commit()

    print(f"Done. Updated {len(updates)} / {total} articles.")


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="Maximum articles to process"
    )
    parser.add_argument(
        "--concurrency", type=int, default=8, help="Concurrent HTTP requests"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(main(limit=args.limit, concurrency=args.concurrency))


if __name__ == "__main__":
    cli()
