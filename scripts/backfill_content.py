"""Backfill markdown_content / image_url / resolved URLs by re-fetching the
original article page for every row whose body is empty or too short.

Usage:
    python -m scripts.backfill_content                  # everything below threshold
    python -m scripts.backfill_content --min-length 600
    python -m scripts.backfill_content --limit 100 --concurrency 6
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from sqlalchemy import func, or_, select

from src.models.article import Article
from src.models.base import get_session_factory
from src.scrapers.article_fetcher import fetch_article

logger = logging.getLogger(__name__)


async def _process(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    article_id: int,
    url: str,
) -> tuple[int, dict | None]:
    async with sem:
        payload = await fetch_article(url, client=client)
        return article_id, payload or None


async def main(min_length: int, limit: int | None, concurrency: int) -> None:
    session_factory = get_session_factory()

    async with session_factory() as session:
        # Match NULL OR empty OR short content directly in SQL so the
        # backfill scales to large tables.
        stmt = (
            select(Article)
            .where(
                or_(
                    Article.markdown_content.is_(None),
                    func.coalesce(func.length(Article.markdown_content), 0) < min_length,
                )
            )
            .order_by(Article.id.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        candidates = result.scalars().all()

    total = len(candidates)
    print(f"Candidates with body shorter than {min_length} chars: {total}")
    if not candidates:
        return

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; AI-News-Bot/1.0; "
                "+https://github.com/anthropics/claude-code)"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        tasks = [_process(sem, client, a.id, a.url) for a in candidates]
        results: list[tuple[int, dict | None]] = []
        for i, coro in enumerate(asyncio.as_completed(tasks), start=1):
            article_id, payload = await coro
            results.append((article_id, payload))
            if i % 10 == 0 or i == total:
                non_empty = sum(
                    1 for _, p in results
                    if p and len(p.get("body_markdown") or "") >= min_length
                )
                print(
                    f"  Progress: {i}/{total} processed, "
                    f"{non_empty} with usable body so far."
                )

    # Persist — one transaction per article so a single failure doesn't roll
    # back the entire batch.
    updated = 0
    failed = 0
    for article_id, payload in results:
        if not payload:
            continue
        async with session_factory() as session:
            article = (
                await session.execute(select(Article).where(Article.id == article_id))
            ).scalar_one_or_none()
            if article is None:
                continue

            body = payload.get("body_markdown") or ""
            changed = False
            if len(body) > len(article.markdown_content or ""):
                article.markdown_content = body
                changed = True

            resolved = payload.get("resolved_url")
            if resolved and resolved != article.url:
                # The unique constraint on Article.url means we can't update
                # to a URL another article already owns. Skip the URL update
                # in that case; content/image still get persisted.
                clash = (
                    await session.execute(
                        select(Article.id).where(
                            Article.url == resolved[:2048],
                            Article.id != article_id,
                        )
                    )
                ).scalar_one_or_none()
                if clash is None:
                    article.url = resolved[:2048]
                    changed = True
                else:
                    logger.info(
                        "URL conflict: article id=%s would clash with id=%s on %s; "
                        "keeping original URL, saving body/image only",
                        article_id, clash, resolved,
                    )

            if payload.get("title") and (
                not article.title or article.title == "Untitled"
            ):
                article.title = payload["title"].strip()[:1024]
                changed = True
            if payload.get("author") and not article.author:
                article.author = payload["author"][:512]
                changed = True
            if payload.get("image_url") and not article.image_url:
                article.image_url = payload["image_url"][:2048]
                changed = True

            if changed:
                try:
                    await session.commit()
                    updated += 1
                except Exception as exc:
                    await session.rollback()
                    failed += 1
                    logger.warning(
                        "Failed to persist article id=%s: %s", article_id, exc
                    )

    print(
        f"\nDone. Persisted updates for {updated} / {total} articles "
        f"({failed} write failures, see logs)."
    )


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-length", type=int, default=400)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(
        main(
            min_length=args.min_length,
            limit=args.limit,
            concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    cli()
