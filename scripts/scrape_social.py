"""Live scrape from social media sources (YouTube, Telegram) + summarize with OpenAI."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.base import get_session_factory
from src.models.article import Article
from src.models.category import Category
from src.models.article_category import ArticleCategory
from src.models.summary import Summary
from src.scrapers.youtube_scraper import YouTubeScraper
from src.scrapers.telegram_scraper import TelegramScraper
from src.scrapers.content_cleaner import ContentCleaner
from src.llm.openai_provider import OpenAIProvider


async def main():
    print("=" * 70)
    print("SOCIAL MEDIA SCRAPE — YouTube + Telegram")
    print("=" * 70)

    session_factory = get_session_factory()
    openai = OpenAIProvider()

    all_articles = []

    # YouTube AI channels
    print("\n--- YouTube AI Channels ---")
    yt = YouTubeScraper(source_config={
        "channel_ids": [
            "UCbfYPyITQ-7l4upoX8nvctg",  # Two Minute Papers
            "UCZHmQk67mSJgfCCTn7xBfew",  # Yannic Kilcher
            "UCNJ1Ymd5yFuUPtn21xtRbbw",  # AI Explained
        ]
    })
    try:
        yt_articles = await yt.scrape()
        print(f"  Found {len(yt_articles)} videos")
        all_articles.extend([("YouTube", a) for a in yt_articles[:5]])
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        await yt.close()

    # Telegram AI channels
    print("\n--- Telegram AI Channels ---")
    tg = TelegramScraper(source_config={
        "channels": ["ai_newz"]
    })
    try:
        tg_articles = await tg.scrape()
        print(f"  Found {len(tg_articles)} posts")
        all_articles.extend([("Telegram", a) for a in tg_articles[:5]])
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        await tg.close()

    print(f"\nTotal to process: {len(all_articles)}")

    processed = 0
    async with session_factory() as session:
        result = await session.execute(select(Category))
        categories = {c.name: c for c in result.scalars().all()}
        category_names = list(categories.keys())

        for source_name, raw in all_articles:
            url_hash = hashlib.sha256(raw.url.encode()).hexdigest()

            existing = await session.execute(
                select(Article).where(Article.url_hash == url_hash)
            )
            if existing.scalar_one_or_none():
                print(f"\n  [SKIP] {raw.title[:50]}...")
                continue

            markdown = ContentCleaner.clean(raw.raw_content) if raw.raw_content else raw.title
            pub_at = raw.published_at
            if pub_at and pub_at.tzinfo is not None:
                pub_at = pub_at.replace(tzinfo=None)

            image_url = raw.metadata.get("image_url") or raw.metadata.get("thumbnail_url")

            article = Article(
                title=raw.title,
                url=raw.url,
                url_hash=url_hash,
                raw_content=raw.raw_content or "",
                markdown_content=markdown,
                author=raw.author,
                published_at=pub_at,
                image_url=image_url,
                pipeline_status="ingested",
            )
            session.add(article)
            await session.flush()

            # Classify
            assigned_cats = []
            if await openai.is_available():
                try:
                    resp = await openai.generate(
                        prompt=f'Classify: "{raw.title}"\nContent: {markdown[:500]}\nCategories: {json.dumps(category_names)}\nJSON: {{"categories": ["Cat1"]}}',
                        system_prompt="Classify into 1-3 categories. JSON only.",
                        json_mode=True,
                    )
                    assigned_cats = json.loads(resp.text).get("categories", [])
                    for cat_name in assigned_cats:
                        if cat_name in categories:
                            session.add(ArticleCategory(article_id=article.id, category_id=categories[cat_name].id))
                except Exception:
                    pass

            # Score
            article.importance_score = 3
            article.pipeline_status = "scored"

            # Summarize
            if await openai.is_available():
                try:
                    resp = await openai.generate(
                        prompt=f'Summarize for AI audience:\nTitle: {raw.title}\nContent: {markdown[:2000]}\nJSON: {{"summary": "...", "headline": "..."}}',
                        system_prompt="2-3 sentences. JSON only.",
                        json_mode=True,
                    )
                    data = json.loads(resp.text)
                    session.add(Summary(
                        article_id=article.id,
                        headline=data.get("headline", raw.title[:100]),
                        summary_text=data.get("summary", ""),
                        llm_provider="openai",
                    ))
                except Exception:
                    session.add(Summary(article_id=article.id, headline=raw.title[:100],
                                       summary_text=raw.title, llm_provider="fallback"))

            article.pipeline_status = "routed"
            processed += 1

            print(f"\n{'─' * 70}")
            print(f"  [{source_name}] {raw.title[:70]}")
            print(f"  CATS: {', '.join(assigned_cats) if assigned_cats else 'N/A'}")
            if image_url:
                print(f"  IMG:  {image_url[:70]}")
            print(f"  URL:  {raw.url}")

        await session.commit()

    await openai.close()
    print(f"\n{'=' * 70}")
    print(f"Done! {processed} new articles from social media.")
    print(f"Dashboard: http://localhost:8080/admin")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
