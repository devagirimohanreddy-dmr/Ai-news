# AI News Aggregator — Microsoft Teams Integration

**Project documentation**
**Author:** Pranavi Sambari
**Last updated:** 13 May 2026
**Status:** Functional end-to-end. Awaiting production hosting.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Goal](#2-project-goal)
3. [Technology Stack](#3-technology-stack)
4. [System Architecture](#4-system-architecture)
5. [Step-by-Step Implementation](#5-step-by-step-implementation)
6. [Current Capabilities](#6-current-capabilities)
7. [Known Gaps & Remaining Work](#7-known-gaps--remaining-work)
8. [Repository Structure](#8-repository-structure)
9. [How to Run Locally](#9-how-to-run-locally)
10. [Production Deployment — The Final Step](#10-production-deployment--the-final-step)
11. [Appendix — Glossary & References](#11-appendix--glossary--references)

---

## 1. Executive Summary

This project delivers an automated news aggregation pipeline that pushes curated AI/tech news into a Microsoft Teams channel as visually rich Adaptive Cards.

**Headline capabilities**

- Scrapes 18 different news sources on individually-tuned schedules.
- Resolves Google News redirect URLs to the underlying publisher article using Google's internal `batchexecute` RPC.
- Extracts full article content (body, hero image, author, publish date) from each publisher page.
- Generates a one-line headline and 2–3 sentence summary for every article using a local LLM (Ollama / `llama3.2:3b`).
- Stores ~1,600 articles in PostgreSQL with searchable markdown bodies.
- Exposes an admin dashboard for managing sources, browsing articles, and tuning behaviour.
- Pushes notifications for 7 hand-selected premium sources into a Teams channel via Power Automate.

**Volume metrics** (as of writing)

| Metric | Value |
|---|---|
| Articles processed | 1,615 |
| Articles with hero image | 1,419 (87.9%) |
| Articles with body ≥ 400 chars | 1,458 (90.3%) |
| Articles with AI summary | 1,615 (100.0%) |
| Sources actively scraped | 18 |
| Sources allowlisted for Teams notifications | 7 |

**Status:** end-to-end pipeline is working in development. The only remaining task is to deploy the backend to an always-on host so notifications continue arriving when the developer's laptop is off.

---

## 2. Project Goal

Build an internal Microsoft Teams notification system that delivers curated AI / tech news article previews directly into a Teams channel, with:

- **Headline** prominently displayed
- **2-line content preview**
- **"Read more" button** that opens the article at the publisher's site
- **Real article images** as card thumbnails wherever possible
- **Automatic operation** — no human intervention to push notifications
- **Source curation** — only from a small allowlist of premium publishers (not all sources notify)

---

## 3. Technology Stack

### 3.1 Backend

| Component | Technology | Purpose |
|---|---|---|
| Language | Python 3.12 | All backend logic |
| Web framework | FastAPI | REST API + admin dashboard routes |
| ASGI server | Uvicorn | Async HTTP server |
| ORM | SQLAlchemy 2.x (async) | Database access |
| Database | PostgreSQL 16 | Persistent storage |
| Migrations | Alembic | Schema versioning |
| DB driver | asyncpg | Async PostgreSQL client |
| Cache & message broker | Redis 7 | Celery broker + response cache |
| Task queue | Celery (worker + embedded beat) | Scheduled scrapes, background work |
| Cron scheduling | croniter | Per-source schedule gating |
| Configuration | Pydantic Settings + `.env` | Runtime configuration |
| Logging | structlog | Structured JSON logs |

### 3.2 LLM / AI

| Component | Technology | Purpose |
|---|---|---|
| Local LLM | Ollama (`llama3.2:3b`) | Article summarization and scoring |
| Optional cloud LLMs | OpenAI / Anthropic / Google Generative AI SDKs | Drop-in replacements if API keys provided |

### 3.3 Scraping & Content Extraction

| Component | Technology | Purpose |
|---|---|---|
| HTTP client | httpx (async) | All outbound requests |
| RSS parsing | feedparser | Generic feed handling |
| Article content extraction | python-readability | Strip ads/nav/chrome, extract main article |
| HTML → Markdown | markdownify | Convert clean HTML to markdown for storage |
| Markdown → HTML (display) | markdown | Render stored markdown back to HTML |
| Browser automation | Playwright + Chromium (headless) | Last-resort image extraction for JS-rendered / anti-bot sites |
| HTML attribute parsing | Custom regex (no BeautifulSoup) | Image discovery, JSON-LD walking, meta-tag extraction |

### 3.4 Frontend (admin dashboard)

| Component | Technology | Purpose |
|---|---|---|
| Server-side templating | Jinja2 | All HTML pages |
| Interactivity | HTMX 1.9 | Live refresh on stats without JS framework |
| Icons | Lucide (CDN) | UI iconography |
| Fonts | Inter (Google Fonts) | Typography |
| CSS | Hand-written with CSS custom properties | Light/dark theme |

**Design choice:** server-rendered HTML — no React, Vue, or Next.js. Keeps the stack simple and dependency-light.

### 3.5 Teams Integration

| Component | Technology | Purpose |
|---|---|---|
| Public tunnel (dev) | ngrok | Expose localhost backend to the internet |
| Notification trigger | Microsoft Power Automate | Cloud flow that polls our RSS feed |
| Card format | Adaptive Cards 1.4 | Visual rendering inside Teams |
| Posting mechanism | Power Automate "Post card in chat or channel" action | Uses Flow bot identity, authenticated as the flow owner |

### 3.6 Infrastructure

| Component | Technology | Purpose |
|---|---|---|
| Containerization | Docker | Each service runs in its own container |
| Orchestration | Docker Compose | 5-container stack defined in `docker-compose.yml` |
| Services | `app`, `celery-worker`, `postgres`, `redis`, `ollama` | All running locally during development |

---

## 4. System Architecture

```
                      ┌───────────────────────────────────────────┐
                      │   18 news sources — RSS, Hacker News,     │
                      │   Reddit, arXiv, Twitter, YouTube,        │
                      │   Telegram, NewsAPI, GitHub, etc.         │
                      └─────────────────┬─────────────────────────┘
                                        │  scheduled scrapes (Celery)
                                        ▼
            ┌──────────────────────────────────────────────────────┐
            │   Celery Beat — ticks every 15 minutes               │
            │   Dispatcher gates each source by its cron schedule  │
            └────────────────────────┬─────────────────────────────┘
                                     │
                                     ▼
            ┌──────────────────────────────────────────────────────┐
            │   Celery Worker — runs scrape jobs in parallel       │
            │   Each job feeds raw articles to the pipeline        │
            └────────────────────────┬─────────────────────────────┘
                                     │
                                     ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │ Six-Stage Pipeline (per article)                                   │
   │                                                                    │
   │ 1. Ingest    — resolve Google News URL, fetch publisher page,      │
   │                extract content, image, author, publish date        │
   │ 2. Dedupe    — SHA-256 hash + simhash check against existing       │
   │ 3. Classify  — LLM assigns categories (AI, security, ML, etc.)     │
   │ 4. Score     — LLM importance score 1-10                            │
   │ 5. Summarize — LLM generates headline + 2-3 sentence summary       │
   │ 6. Route     — mark as 'routed', enqueue digest, push to Teams     │
   └────────────────────────┬───────────────────────────────────────────┘
                            │
                            ▼
                ┌─────────────────────────────┐
                │   PostgreSQL Database       │
                │  - articles, sources        │
                │  - summaries, categories    │
                │  - post_log, subscriptions  │
                └────┬───────────────────┬────┘
                     │                   │
       ┌─────────────┘                   └──────────────────────────┐
       ▼                                                            ▼
┌──────────────────────────┐              ┌──────────────────────────────────────┐
│ Admin Dashboard          │              │ /admin/api/teams/feed.rss            │
│ http://localhost:8080/   │              │  - filters to allowlisted sources    │
│ admin/                   │              │  - JSON-safe sanitized content       │
│  - Overview              │              │  - Token-protected URL parameter     │
│  - Sources               │              └──────────────┬───────────────────────┘
│  - Articles              │                             │
│  - Categories            │                             ▼  ( ngrok HTTPS tunnel )
│  - Logs                  │              ┌──────────────────────────────────────┐
│  - Commands              │              │ Power Automate (Microsoft cloud)     │
│  - Diagnostics           │              │  - Polls every ~15-30 minutes        │
│  - Settings              │              │  - Detects new items by pubDate      │
└──────────────────────────┘              │  - Builds Adaptive Card per item     │
                                          │  - Posts to Teams channel as bot     │
                                          └──────────────┬───────────────────────┘
                                                         │
                                                         ▼
                            ┌──────────────────────────────────────────────────┐
                            │ Microsoft Teams — `ai-news-test` channel         │
                            │                                                  │
                            │   ┌──────────────────────────────────────────┐   │
                            │   │ AI NEWS                                  │   │
                            │   │ Article Headline                         │   │
                            │   │ Two-line preview text of summary…        │   │
                            │   │ [ Read more → ]                          │   │
                            │   └──────────────────────────────────────────┘   │
                            └──────────────────────────────────────────────────┘
```

---

## 5. Step-by-Step Implementation

The project was built in four major phases. Below is a detailed account of each.

### Phase 1 — Baseline Setup and UI Modernization

**Goal:** Get the existing codebase running cleanly and modernize the admin UI.

| # | Step | Outcome |
|---|---|---|
| 1.1 | Verified Docker Compose stack runs all 5 services healthy: `app`, `celery-worker`, `postgres`, `redis`, `ollama` | Baseline established |
| 1.2 | Rewrote `src/dashboard/static/style.css` end-to-end — light/dark themes, sidebar, stat cards, responsive grid | Modern professional UI |
| 1.3 | Added live HTMX-driven stats refresh on the overview page | Real-time feel without a JavaScript framework |
| 1.4 | Article list — added image thumbnails on cards, hover effects, importance score bars, status badges | Magazine-feel list view |

### Phase 2 — Article Content & Image Pipeline (The Largest Body of Work)

**Goal:** Reliably extract real article content and hero images from every source, including notoriously hard-to-scrape sites and Google News redirect URLs.

#### 2.1 — Google News URL Resolution

Modern Google News RSS feeds use opaque URLs like:
```
https://news.google.com/rss/articles/CBMi…<base64>…?oc=5
```
These cannot be resolved by simply following HTTP redirects. Google now requires an internal RPC call to translate them.

**Solution implemented in `src/scrapers/article_fetcher.py`:**

1. Fetch the Google News page HTML.
2. Parse out three tokens: `data-n-a-sg` (signature), `data-n-a-ts` (timestamp), `data-n-a-id` (article ID).
3. POST those tokens to `https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je` with the correct envelope structure.
4. Parse the response to extract the underlying publisher URL.

**Verification:** Tested live against a real Google News URL → successfully resolved to `cbsnews.com/live-updates/iran-war-trump-oil-prices...`.

#### 2.2 — Multi-Strategy Image Extraction

Built `src/scrapers/image_extractor.py` with a fallback chain that achieves 88% coverage across diverse publishers:

1. **First large image inside `<article>` / `<main>` / common content divs** (highest priority — most accurate)
2. **`<meta property="og:image">`** — but only if not a known site-logo pattern
3. **JSON-LD `application/ld+json`** `image` field (schema.org)
4. **`<meta name="twitter:image">`**
5. **Playwright headless Chromium fallback** — for sites that block static fetches or render images via JavaScript

**Logo / placeholder rejection** — patterns like `arxiv-logo-fb.png`, `defaultPromoCrop.png`, `og-default.png`, `nameplate.png`, etc. are detected and skipped.

**Browser-like User-Agent** with `Sec-Fetch-*` headers bypasses anti-bot filters on sites like Venturebeat, OpenAI, PCMag.

**Post-storage deduplication** — any image URL appearing on ≥ 3 articles is nulled (catches site-wide share banners that slip through individual filters).

#### 2.3 — Full Article Content Extraction

Built `src/scrapers/article_fetcher.py::fetch_article()`:

1. Resolves Google News URLs (or uses input URL).
2. Fetches publisher page with browser-like headers.
3. Runs `python-readability` to extract main content.
4. Falls back to manual `<article>` / `<main>` block selection if readability returns empty.
5. Converts extracted HTML to markdown via `markdownify`.
6. Extracts metadata: `og:title`, `og:description`, `article:author`, `article:published_time`.

#### 2.4 — Backfill Scripts

Created `scripts/backfill_content.py` and `scripts/backfill_images.py` to re-process the existing 1,600 articles with the new extraction logic. Run incrementally with concurrency controls.

#### 2.5 — Article Detail Page Rendering

Switched from client-side `marked.js` (CDN dependency) to server-side rendering with Python's `markdown` library. Eliminates client-side failure modes. Article reader now resembles a professional blog with proper typography, hero image, prose-style headings, lists, links, blockquotes, code blocks, and tables.

#### 2.6 — Diagnostic Endpoints

Added `src/dashboard/api/diagnostics.py` with endpoints:

- `GET /admin/api/diagnostics/images` — image coverage statistics + sample
- `GET /admin/api/diagnostics/content` — body length statistics + sample
- `GET /admin/api/diagnostics/resolve?url=…` — test Google News resolution on any URL
- `GET /admin/api/diagnostics/fetch?url=…` — full end-to-end extraction test
- `POST /admin/api/diagnostics/content/refetch/{id}` — re-fetch a specific article
- `POST /admin/api/diagnostics/teams/test` — send test card to Teams webhook

### Phase 3 — Per-Source Scheduling

**Goal:** Match scrape frequency to each source's actual publishing rate. Avoid wasted requests and rate-limit risk.

#### 3.1 — Per-Source Cron Configuration

Each entry in the `sources` table has a `schedule_cron` column. Updated all 22 sources with appropriate schedules based on publishing velocity.

#### 3.2 — Celery Beat Gating with `croniter`

Modified `src/scheduler/scrape_tasks.py::scrape_all_sources`:

1. Celery beat ticks every 15 minutes.
2. On each tick, dispatcher queries all enabled sources.
3. For each source, uses `croniter` to check if the source's cron expression is due given its `last_scraped_at`.
4. Only dispatches scrape jobs for due sources.

This honors per-source schedules with a 15-minute granularity floor.

#### 3.3 — Final Scrape Schedule

| # | Source | Cron | Frequency |
|---|---|---|---|
| 1 | Hacker News | `*/15 * * * *` | every 15 min |
| 2 | Twitter AI News | `0 * * * *` | every hour |
| 3 | NewsAPI AI/Tech | `0 */2 * * *` | every 2 hours |
| 4 | Reddit r/technology | `0 */2 * * *` | every 2 hours |
| 5 | TechCrunch | `*/30 * * * *` | every 30 min |
| 6 | The Verge | `*/30 * * * *` | every 30 min |
| 7 | VentureBeat | `*/30 * * * *` | every 30 min |
| 8 | Reddit r/MachineLearning | `0 */3 * * *` | every 3 hours |
| 9 | Reddit r/artificial | `0 */3 * * *` | every 3 hours |
| 10 | Ars Technica | `0 * * * *` | every hour |
| 11 | Telegram AI Channels | `0 */4 * * *` | every 4 hours |
| 12 | Wired | `0 * * * *` | every hour |
| 13 | MIT Technology Review | `0 */2 * * *` | every 2 hours |
| 14 | arXiv AI/ML | `0 */12 * * *` | every 12 hours |
| 15 | GitHub Trending AI | `0 6 * * *` | daily 06:00 UTC |
| 16 | LinkedIn AI Companies | `0 6 * * *` | daily 06:00 UTC |
| 17 | YouTube AI Channels | `0 6 * * *` | daily 06:00 UTC |
| 18 | open AI blog | `0 8 * * *` | daily 08:00 UTC |

#### 3.4 — `notify_to_teams` Source Flag

Added a `notify_to_teams` boolean column to `sources` (Alembic migration `0004`). Set to `True` for 7 hand-picked premium sources:

- Hacker News
- TechCrunch
- The Verge
- VentureBeat
- Ars Technica
- Wired
- MIT Technology Review

Only articles from these 7 sources are eligible for Teams notifications.

#### 3.5 — Source Linkage Backfill

Fixed a legacy issue where all 1,640 articles had `source_id = NULL`. Mapped each article to its source by URL domain pattern. Successfully linked 1,490 articles. Updated the ingest pipeline so future articles auto-link.

### Phase 4 — Microsoft Teams Integration

**Goal:** Deliver curated article notifications into a Teams channel as Adaptive Cards.

#### 4.1 — Architecture Decision: RSS Pull, Not Webhook Push

The original plan was for our backend to POST to a Power Automate webhook URL. Problem: our tenant's Power Automate generates only "Direct API" URLs that require OAuth, and we cannot register an Azure AD app to obtain OAuth credentials.

**Solution:** invert the data flow. Build an RSS feed endpoint on our server. Have Power Automate's standard "When a feed item is published" trigger poll it. This requires zero authentication on either side — Power Automate uses the flow owner's identity to post to Teams.

#### 4.2 — RSS Feed Endpoint

Built `src/dashboard/api/teams_feed.py`:

- URL: `/admin/api/teams/feed.rss?token=<secret>`
- RSS 2.0 format with standard fields
- Filters to articles whose source is `notify_to_teams = True` AND `importance_score >= TEAMS_NOTIFICATION_MIN_SCORE`
- Limits to 50 most recent items
- Caps at items < 7 days old
- Token-protected so the feed isn't world-readable

**Critical detail — JSON-safe sanitization:** Every field is run through a sanitizer that:

- Replaces smart curly quotes with ASCII equivalents
- Replaces double quotes `"` with single quotes
- Removes backslashes
- Collapses newlines, tabs, control characters to single spaces
- Collapses runs of whitespace

This is necessary because Power Automate substitutes RSS field values directly into the Adaptive Card JSON template. Without sanitization, a smart quote in an article title can break the entire JSON body and Teams rejects the card with `InvalidBotRequestMessageBody`.

#### 4.3 — Power Automate Flow Setup

- **Trigger:** "When a feed item is published"
- **Feed URL:** points at our RSS endpoint (via ngrok tunnel)
- **Chosen property:** `PublishDate` (used for new-item detection)
- **Action:** "Post card in a chat or channel"
- **Post as:** Flow bot
- **Channel:** `ai-news-test` (private channel for safe testing)

#### 4.4 — Adaptive Card Template

```json
{
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "type": "AdaptiveCard",
  "version": "1.4",
  "body": [
    {
      "type": "TextBlock",
      "text": "AI NEWS",
      "weight": "Bolder",
      "size": "Small",
      "color": "Accent",
      "isSubtle": true,
      "spacing": "None"
    },
    {
      "type": "TextBlock",
      "text": "@{coalesce(triggerOutputs()?['body/title'], 'New article')}",
      "weight": "Bolder",
      "size": "Large",
      "wrap": true,
      "spacing": "Small"
    },
    {
      "type": "TextBlock",
      "text": "@{coalesce(triggerOutputs()?['body/summary'], '')}",
      "wrap": true,
      "isSubtle": true,
      "spacing": "Small",
      "maxLines": 2
    }
  ],
  "actions": [
    {
      "type": "Action.OpenUrl",
      "title": "Read more",
      "url": "@{coalesce(triggerOutputs()?['body/primaryLink'], 'https://example.com')}"
    }
  ]
}
```

**Why `coalesce()`?** Power Automate substitutes placeholders as literal text. If a field is missing, the substitution produces `null` — invalid for an Adaptive Card text field and invalid as an `Action.OpenUrl` target. The `coalesce()` function provides a safe fallback string.

#### 4.5 — ngrok HTTPS Tunnel

Power Automate runs in Microsoft's cloud and must reach our backend at `localhost:8080`. ngrok provides a free HTTPS tunnel:

```
ngrok http 8080
```

This exposes a public URL like `https://casualty-tractor-tusk.ngrok-free.dev` that forwards to local port 8080. The Power Automate flow's RSS URL uses this public URL.

#### 4.6 — Private Test Channel

To avoid spamming the team's General channel during validation, all testing is done in a private channel `ai-news-test` inside the team `Linkfields General and IT`. Only explicit members can see it.

#### 4.7 — End-to-End Verification

Confirmed working flow:

1. Article published on a real source (e.g., Wired).
2. Backend scrapes it on schedule.
3. Pipeline ingests, scores, summarizes, marks as routed.
4. Article appears in `/admin/api/teams/feed.rss` within seconds.
5. Power Automate polls the feed within 5–30 minutes.
6. New item detected by pubDate.
7. Adaptive Card posted to `ai-news-test`.
8. Card displays headline, 2-line summary, working Read More button.

**Issues encountered and resolved:**

- ngrok URL rotated between sessions → updated flow URL.
- Initial card template failed with `InvalidBotRequestMessageBody` due to special characters in resolved field values → added server-side JSON-safe sanitization.
- Empty `primaryLink` field caused `Action.OpenUrl` to reject empty URL → wrapped in `coalesce()`.
- Some scrapes produced UniqueViolationError on URL conflicts (Google News + direct URLs resolving to the same publisher URL) → adjusted ingest stage to commit per article rather than per batch.

---

## 6. Current Capabilities

| Capability | Status | Notes |
|---|---|---|
| Article ingestion from 18 sources | ✅ working | All running on individual cron schedules |
| Google News URL resolution | ✅ working | Verified via batchexecute RPC |
| Full article body extraction | ✅ 90% success | Some sites genuinely have no body content (Reddit text posts, etc.) |
| Hero image extraction | ✅ 88% success | Best-in-class multi-strategy fallback chain |
| LLM-generated headline + summary | ✅ 100% | Every article has a summary |
| LLM-assigned categories | ✅ 100% | Every article has categories |
| LLM-assigned importance score | ✅ 100% | Currently 1–2 range (LLM scoring is conservative) |
| Per-source scrape schedules | ✅ working | croniter-gated dispatcher |
| Admin dashboard | ✅ working | Stats, articles, sources, settings, logs, commands, diagnostics |
| Article detail page with markdown rendering | ✅ working | Hero image + prose-style article body |
| Teams integration end-to-end | ✅ working | Cards land in `ai-news-test` automatically |
| Diagnostic endpoints | ✅ working | For runtime debugging |

---

## 7. Known Gaps & Remaining Work

| Gap | Impact | Path to Fix |
|---|---|---|
| **Backend depends on developer laptop** | System stops when laptop sleeps or shuts down | Deploy backend to a small always-on server / VM |
| **ngrok URL rotates on restart** | Power Automate flow's URL must be updated manually after each ngrok restart | Replace ngrok with Cloudflare Tunnel (stable URL, free, no card) OR deploy to a host with a permanent URL |
| **API keys missing for some sources** | NewsAPI, Twitter, and 4 Firecrawl-based blogs (Anthropic, DeepMind, Meta, OpenAI Blog) produce zero articles without keys | Provide keys when available; functionality is built-in |
| **Notifications without admin Entra ID** | Could not register an Azure AD app for OAuth-protected webhooks; resolved by switching to RSS-pull architecture | No longer a blocker |
| **Source-name kicker in card is static "AI NEWS"** | Cards don't show which source the article came from | One-line change to card template using RSS `categories[0]` field with `coalesce()` |
| **Hero image not in card** | Cards show only text, not the article's image | Requires server-side RSS structure change to expose image in a Power-Automate-readable field |

---

## 8. Repository Structure

```
Ai-news/
├── PROJECT_DOCUMENTATION.md          ← this file
├── README.md
├── pyproject.toml                    ← Python dependencies
├── docker-compose.yml                ← 5-container stack
├── .env                              ← runtime secrets (not in git)
│
├── alembic/                          ← database migrations
│   ├── env.py
│   └── versions/
│       ├── 0001_initial_schema.py
│       ├── 0002_add_trgm_and_fulltext_indexes.py
│       ├── 0003_add_image_url_to_articles.py
│       └── 0004_add_notify_to_teams.py
│
├── src/
│   ├── main.py                       ← FastAPI app entry point
│   │
│   ├── config/
│   │   ├── settings.py               ← Pydantic Settings from .env
│   │   ├── celery_app.py             ← Celery + beat schedule (15-min tick)
│   │   ├── logging.py
│   │   └── health.py                 ← /health endpoint
│   │
│   ├── models/                       ← SQLAlchemy models
│   │   ├── base.py
│   │   ├── article.py
│   │   ├── source.py
│   │   ├── category.py
│   │   ├── summary.py
│   │   ├── subscription.py
│   │   └── post_log.py
│   │
│   ├── pipeline/                     ← 6-stage article processing
│   │   ├── base.py
│   │   ├── orchestrator.py
│   │   ├── ingest.py                 ← stage 1
│   │   ├── dedup.py                  ← stage 2
│   │   ├── classify.py               ← stage 3
│   │   ├── score.py                  ← stage 4
│   │   ├── summarize.py              ← stage 5
│   │   └── route.py                  ← stage 6
│   │
│   ├── scrapers/
│   │   ├── base.py                   ← BaseScraper + RawArticle
│   │   ├── registry.py               ← type → scraper class map
│   │   ├── article_fetcher.py        ← full-page fetcher + Google News resolver
│   │   ├── image_extractor.py        ← multi-strategy image extraction
│   │   ├── content_cleaner.py        ← readability + markdownify
│   │   ├── rss_scraper.py
│   │   ├── hn_scraper.py
│   │   ├── reddit_scraper.py
│   │   ├── arxiv_scraper.py
│   │   ├── youtube_scraper.py
│   │   ├── newsapi_scraper.py
│   │   ├── telegram_scraper.py
│   │   ├── linkedin_scraper.py
│   │   ├── twitter_scraper.py
│   │   └── github_scraper.py
│   │
│   ├── scheduler/
│   │   ├── scrape_tasks.py           ← Celery scrape dispatcher with cron gating
│   │   ├── digest_tasks.py
│   │   └── alert_tasks.py
│   │
│   ├── dashboard/
│   │   ├── routes.py                 ← HTML page routes
│   │   ├── api/
│   │   │   ├── stats.py
│   │   │   ├── sources.py
│   │   │   ├── articles.py
│   │   │   ├── categories.py
│   │   │   ├── logs.py
│   │   │   ├── settings_api.py
│   │   │   ├── commands.py
│   │   │   ├── diagnostics.py
│   │   │   └── teams_feed.py         ← /admin/api/teams/feed.rss
│   │   ├── templates/                ← Jinja2 templates
│   │   │   ├── base.html
│   │   │   ├── overview.html
│   │   │   ├── sources.html
│   │   │   ├── articles.html
│   │   │   ├── article_detail.html
│   │   │   ├── categories.html
│   │   │   ├── logs.html
│   │   │   ├── commands.html
│   │   │   └── settings.html
│   │   └── static/
│   │       └── style.css             ← hand-written CSS (~1,500 lines)
│   │
│   ├── bot/                          ← Teams Bot Framework scaffolding (not used in current RSS-pull architecture)
│   │   ├── adapter.py
│   │   ├── bot_app.py
│   │   ├── cards/
│   │   ├── commands/
│   │   └── services/
│   │       └── teams_webhook.py
│   │
│   └── llm/                          ← LLM client adapters
│       ├── router.py
│       ├── ollama_client.py
│       ├── openai_client.py
│       ├── anthropic_client.py
│       └── google_client.py
│
└── scripts/
    ├── backfill_content.py           ← re-fetch full content for existing articles
    ├── backfill_images.py            ← re-extract images for existing articles
    └── scrape_and_summarize.py       ← one-shot CLI scrape
```

---

## 9. How to Run Locally

### Prerequisites

- Docker Desktop installed
- `git`
- 4 GB free RAM
- 5 GB free disk

### First-time setup

```powershell
# Clone the repository
git clone <repo-url>
cd Ai-news

# Copy the env template and fill in any keys (most are optional)
cp .env.example .env

# Build and start all services
docker compose up -d

# Run database migrations
docker compose exec app alembic upgrade head

# Pull the Ollama model (one-time, ~2 GB download)
docker compose exec ollama ollama pull llama3.2:3b
```

### Daily use

```powershell
# Start the stack
docker compose up -d

# View logs
docker compose logs -f app
docker compose logs -f celery-worker

# Stop the stack
docker compose down
```

### Open the dashboard

http://localhost:8080/admin

### Set up the Teams integration (one-time)

1. Start ngrok in a terminal:
   ```powershell
   ngrok http 8080
   ```
   Note the `https://*.ngrok-free.dev` URL.

2. Create a private Teams channel for testing (e.g. `ai-news-test`).

3. Create a Power Automate flow:
   - Trigger: `When a feed item is published`
   - URL: `https://<ngrok-subdomain>.ngrok-free.dev/admin/api/teams/feed.rss?token=<value-of-TEAMS_FEED_TOKEN-in-.env>`
   - Chosen property: `PublishDate`
   - Action: `Post card in a chat or channel`
   - Channel: `ai-news-test`
   - Adaptive Card: paste the template from section 4.4

4. Save the flow. Wait 5–30 minutes for the first scheduled poll, or click `Test → Manually → Run flow`.

---

## 10. Production Deployment — The Final Step

The integration is complete and working in development. The only remaining task is **moving the backend off the developer's laptop** so it operates 24/7 independently.

### What is needed

A small Linux server with these specs:

- 2 CPU cores
- 2 GB RAM
- 20 GB disk
- Docker installed
- Internet connectivity
- A stable public URL (or use Cloudflare Tunnel — free, no card)

### Options ranked by ease and cost

| Option | Cost | Notes |
|---|---|---|
| Internal company VM | Variable — often free for internal projects | Best path. Standard ask for any internal tool. |
| Oracle Cloud Always Free tier | Free (forever, but card required for signup verification) | Real production-grade VM, never charges |
| Railway / Render / Fly.io | $5–15 / month | Push-to-deploy, very easy |
| Existing always-on machine (home / office) | $0 | A spare desktop or Raspberry Pi works fine |

### Deployment process (any of the above)

```
1. Provision the server.
2. Install Docker.
3. Clone the repo.
4. Copy .env across (use the same TEAMS_FEED_TOKEN value).
5. Run `docker compose up -d` and `alembic upgrade head`.
6. Set up Cloudflare Tunnel for a stable HTTPS URL, OR use the server's
   own public IP / domain if it has one.
7. Update the Power Automate flow's RSS URL to use the new permanent URL.
8. Power off the developer laptop. System keeps running.
```

### Ask to send to IT / mentor

> The AI News Teams integration is fully working end-to-end. Articles flow into our Teams channel automatically from 7 curated sources. The system needs to move off my laptop to operate 24/7.
>
> Could we provision a small Linux VM (2 CPU / 2 GB RAM / 20 GB disk, Docker capable)? This could be on our Azure / AWS subscription (~₹400 / month) or any spare internal server. Code is containerized — deployment is `git clone + docker compose up`. Happy to do the deployment myself.

---

## 11. Appendix — Glossary & References

### Glossary

| Term | Meaning |
|---|---|
| Adaptive Card | A JSON-defined visual card format used by Microsoft Teams, Outlook, etc. |
| Celery | Python distributed task queue used here for scheduled scrapes |
| Cron expression | A 5-field text format (`* * * * *`) defining a recurring schedule |
| Docker Compose | Multi-container Docker orchestration via `docker-compose.yml` |
| Flow bot | The system identity Power Automate uses when posting messages on a user's behalf |
| ngrok | A tool that creates a temporary public HTTPS URL forwarding to localhost |
| Ollama | Self-hosted LLM runtime; runs `llama3.2:3b` locally |
| Power Automate | Microsoft's no-code workflow automation platform (formerly Flow) |
| RSS 2.0 | Long-standing XML format for syndicating content feeds |
| Token-protected URL | A URL with a secret query parameter acting as a shared password |

### Key files quick-reference

| Concern | File |
|---|---|
| Where Teams notifications are filtered | `src/dashboard/api/teams_feed.py` |
| Where Adaptive Card content fields originate | `src/dashboard/api/teams_feed.py` (`_short_preview`, `_json_safe`) |
| Where source schedules live | `sources.schedule_cron` column in DB |
| Where allowlist is defined | `sources.notify_to_teams` column in DB |
| Where Celery beat tick is set | `src/config/celery_app.py` |
| Where cron-gating decision is made | `src/scheduler/scrape_tasks.py::_is_due` |
| Where Google News resolution happens | `src/scrapers/article_fetcher.py::resolve_google_news_url` |
| Where image extraction strategies are defined | `src/scrapers/image_extractor.py::fetch_article_image` |
| Diagnostic endpoints | `src/dashboard/api/diagnostics.py` |

### Useful commands

```powershell
# Tail celery scrape activity
docker compose logs -f celery-worker | findstr "Beat tick"

# Force a single source to scrape now (for testing)
docker compose exec app python -m scripts.scrape_and_summarize --source-id <id>

# Backfill images for articles without them
docker compose exec app python -m scripts.backfill_images --limit 50

# Check image / content coverage
curl http://localhost:8080/admin/api/diagnostics/images
curl http://localhost:8080/admin/api/diagnostics/content

# Send a test card to the Teams webhook (legacy webhook path, currently unused)
curl -X POST http://localhost:8080/admin/api/diagnostics/teams/test
```

### Final status

- ✅ Backend functioning end-to-end.
- ✅ Cards appearing in Teams private channel automatically.
- ✅ All 7 allowlisted sources flowing through the pipeline.
- ⚠️ Production hosting is the single remaining task.

End of document.
