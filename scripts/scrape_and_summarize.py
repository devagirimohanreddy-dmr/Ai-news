"""Scrape news from RSS sources, run through pipeline, and print summaries."""

import asyncio
import json
import sys

from src.models.base import get_session_factory, get_engine, Base
from src.models.article import Article
from src.models.summary import Summary
from src.models.category import Category
from src.models.article_category import ArticleCategory
from src.scrapers.rss_scraper import RssScraper
from src.scrapers.hn_scraper import HackerNewsScraper
from src.scrapers.content_cleaner import ContentCleaner
from src.llm.openai_provider import OpenAIProvider
from src.llm.base import LLMResponse
from src.config.settings import settings

from sqlalchemy import select, text
import hashlib
from datetime import datetime, timezone


async def main():
    print("=" * 70)
    print("AI NEWS AGGREGATOR BOT — Live Scrape & Summarize")
    print("=" * 70)

    # Connect to DB
    session_factory = get_session_factory()

    # Initialize OpenAI
    openai = OpenAIProvider()
    if not await openai.is_available():
        print("ERROR: OpenAI API key not set!")
        return

    print(f"\nOpenAI provider ready (model: gpt-4o-mini)")

    # --- Scrape RSS feeds ---
    rss_sources = [
        {"name": "TechCrunch", "feed_url": "https://techcrunch.com/feed/"},
        {"name": "The Verge", "feed_url": "https://www.theverge.com/rss/index.xml"},
        {"name": "Ars Technica", "feed_url": "https://feeds.arstechnica.com/arstechnica/index"},
    ]

    all_articles = []

    for source in rss_sources:
        print(f"\n--- Scraping {source['name']} ---")
        scraper = RssScraper(source_config=source)
        try:
            articles = await scraper.scrape()
            print(f"  Found {len(articles)} articles")
            # Take top 3 from each source
            all_articles.extend([(source["name"], a) for a in articles[:3]])
        except Exception as e:
            print(f"  Error: {e}")
        finally:
            await scraper.close()

    # --- Scrape Hacker News ---
    print(f"\n--- Scraping Hacker News ---")
    hn_scraper = HackerNewsScraper(source_config={"story_type": "top", "limit": 10})
    try:
        hn_articles = await hn_scraper.scrape()
        print(f"  Found {len(hn_articles)} articles")
        all_articles.extend([("Hacker News", a) for a in hn_articles[:3]])
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        await hn_scraper.close()

    print(f"\n{'=' * 70}")
    print(f"Total articles to process: {len(all_articles)}")
    print(f"{'=' * 70}")

    # --- Process through pipeline (manual for demo) ---
    processed = 0
    async with session_factory() as session:
        # Load categories for classification
        result = await session.execute(select(Category))
        categories = {c.name: c for c in result.scalars().all()}
        category_names = list(categories.keys())

        for source_name, raw in all_articles:
            url_hash = hashlib.sha256(raw.url.encode()).hexdigest()

            # Check for duplicate
            existing = await session.execute(
                select(Article).where(Article.url_hash == url_hash)
            )
            if existing.scalar_one_or_none():
                print(f"\n  [SKIP] Already exists: {raw.title[:60]}...")
                continue

            # Clean content
            markdown = ContentCleaner.clean(raw.raw_content) if raw.raw_content else ""

            # Create article
            # Ensure published_at is timezone-naive for PostgreSQL
            pub_at = raw.published_at
            if pub_at and pub_at.tzinfo is not None:
                pub_at = pub_at.replace(tzinfo=None)

            article = Article(
                title=raw.title,
                url=raw.url,
                url_hash=url_hash,
                raw_content=raw.raw_content or "",
                markdown_content=markdown,
                author=raw.author,
                published_at=pub_at,
                pipeline_status="ingested",
            )
            session.add(article)
            await session.flush()

            # --- Classify via OpenAI ---
            try:
                classify_prompt = f"""Given this article title and content, assign it to 1-3 categories from this list:
{json.dumps(category_names)}

Article title: {raw.title}
Article content (first 500 chars): {markdown[:500]}

Respond in JSON: {{"categories": ["Category Name 1", "Category Name 2"]}}"""

                classify_resp = await openai.generate(
                    prompt=classify_prompt,
                    system_prompt="You are a news classifier. Respond only in valid JSON.",
                    json_mode=True,
                )
                cat_data = json.loads(classify_resp.text)
                assigned_cats = cat_data.get("categories", [])

                for cat_name in assigned_cats:
                    if cat_name in categories:
                        session.add(ArticleCategory(
                            article_id=article.id,
                            category_id=categories[cat_name].id,
                        ))

                article.pipeline_status = "classified"
            except Exception as e:
                assigned_cats = ["Uncategorized"]
                print(f"  Classification error: {e}")

            # --- Score (simple keyword-based for speed) ---
            breaking_keywords = ["GPT-5", "GPT-6", "acquired", "acquisition", "vulnerability", "breakthrough"]
            score = 3  # base score
            title_lower = raw.title.lower()
            for kw in breaking_keywords:
                if kw.lower() in title_lower:
                    score += 2
            article.importance_score = min(score, 10)
            article.is_breaking = score >= 8
            article.pipeline_status = "scored"

            # --- Summarize via OpenAI ---
            try:
                summary_prompt = f"""Summarize this article in 2-3 sentences for a technical AI audience. Focus on what's new and why it matters. Also provide a one-line headline.

Title: {raw.title}
Content: {markdown[:2000]}

Respond in JSON: {{"summary": "...", "headline": "..."}}"""

                summary_resp = await openai.generate(
                    prompt=summary_prompt,
                    system_prompt="You are a technical news summarizer. Respond only in valid JSON.",
                    json_mode=True,
                )
                sum_data = json.loads(summary_resp.text)

                summary = Summary(
                    article_id=article.id,
                    headline=sum_data.get("headline", raw.title[:100]),
                    summary_text=sum_data.get("summary", "No summary available."),
                    llm_provider="openai",
                )
                session.add(summary)
                article.pipeline_status = "summarized"
            except Exception as e:
                print(f"  Summarization error: {e}")
                summary = Summary(
                    article_id=article.id,
                    headline=raw.title[:100],
                    summary_text="Summary generation failed.",
                    llm_provider="fallback",
                )
                session.add(summary)

            article.pipeline_status = "routed"
            processed += 1

            # Print result
            print(f"\n{'─' * 70}")
            print(f"  SOURCE: {source_name}")
            print(f"  TITLE:  {raw.title[:80]}")
            print(f"  CATS:   {', '.join(assigned_cats)}")
            print(f"  SCORE:  {article.importance_score}/10 {'🚨 BREAKING' if article.is_breaking else ''}")
            print(f"  URL:    {raw.url}")
            if hasattr(summary, 'headline'):
                print(f"  HEAD:   {summary.headline}")
                print(f"  SUMM:   {summary.summary_text}")

        await session.commit()

    await openai.close()

    print(f"\n{'=' * 70}")
    print(f"DONE! Processed {processed} articles, stored in database.")
    print(f"Dashboard: http://localhost:8080/admin")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
