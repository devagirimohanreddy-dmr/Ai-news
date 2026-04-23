# AI News Aggregator Bot — Design Specification

## Overview

An automated bot that scrapes AI, tech, and open-source news from multiple sources, summarizes articles using multiple LLMs, and posts curated content to a Microsoft Teams channel. Serves a company-wide audience (50+ people) with daily digests and breaking news alerts.

## Architecture

**Pattern:** Modular Monolith — single deployable Python app with clearly separated internal modules. Uses Celery + Redis for async task scheduling. Can evolve into microservices if needed.

**Framework:** FastAPI (Python)

**Docker Compose — 6 containers:**

| Container | Purpose |
|-----------|---------|
| `app` | FastAPI — bot endpoint, dashboard, API |
| `celery-worker` | Async task processing (scraping, summarization, posting) |
| `postgres` | Article storage, search, audit logs |
| `redis` | Celery task queue, caching, rate limiting |
| `firecrawl` | Self-hosted Firecrawl instance for JS-heavy scraping |
| `ollama` | Local LLM for classification and fallback summarization |

**Internal Modules:**

1. **Scrapers** — pluggable adapters for each source type
2. **Summarizer** — multi-LLM router with fallback chain
3. **Pipeline** — article processing: ingest → dedup → classify → score → summarize → route
4. **Teams Bot** — Bot Framework integration, Adaptive Cards, command handling
5. **Scheduler** — Celery Beat cron jobs for scraping and digest generation
6. **Dashboard** — admin web UI for monitoring and configuration

## Scraping Stack

5-layer approach — use the lightest tool that works for each source:

### Layer 1: RSS / Atom Feeds (feedparser) — ~60% of sources

Primary scraping method. Most news sites offer RSS feeds.

**Sources:** TechCrunch, The Verge, Ars Technica, Wired, MIT Technology Review, VentureBeat, arXiv, AI-specific blogs.

### Layer 2: Native APIs (requests + official SDKs) — ~20% of sources

Structured data, rate-limit friendly, most reliable.

**Sources:**
- GitHub API — releases, trending repos
- Reddit API — r/MachineLearning, r/artificial, r/technology
- Hacker News API (Firebase) — top/new stories
- arXiv API — AI/ML paper feeds

### Layer 3: Firecrawl (self-hosted) — ~15% of sources

Self-hosted instance (AGPL-3.0, free). Converts pages to clean markdown with built-in JS rendering and anti-bot handling. Used for JS-heavy sites without RSS.

**Sources:** Company blogs (OpenAI, Anthropic, Google DeepMind, Meta AI), product announcement pages, sites without RSS.

### Layer 4: Playwright — ~5% of sources

Full headless browser. Last resort for stubborn sites that block Firecrawl. Resource-heavy (~500MB RAM per browser instance).

**Sources:** Paywalled sites, heavy SPAs, sites with cookie walls or infinite scroll.

### Layer 5: Readability + Markdownify — post-processor

Applied across all layers. Strips ads, navigation, boilerplate from HTML and converts to clean markdown. Uses `python-readability` (Mozilla's algorithm) + `markdownify`.

### Scraper Selection Flow

```
New source → Has RSS? → feedparser
                 ↓ no
             Has API? → API client
                 ↓ no
           JS-heavy? → Firecrawl
                 ↓ no
         Simple HTML? → requests + readability
                 ↓ no
        Stubborn site → Playwright

All paths → clean markdown → Article Pipeline
```

### Schedule Strategy

- RSS/webhooks as primary (event-driven where available)
- Scheduled scraping as fallback
- Source-specific intervals configurable from dashboard
- High-priority sources checked more frequently

## Article Pipeline

Six stages that every article passes through:

### Stage 1: Ingest

- Receives raw content from any scraper layer
- Normalizes to common `Article` schema: title, url, source, raw_content, published_at, author
- Converts HTML to markdown via readability + markdownify
- Extracts metadata (images, tags, publish date)
- Stores raw article in PostgreSQL

### Stage 2: Dedup

- **Exact match:** URL hash lookup in PostgreSQL
- **Fuzzy match:** Title similarity using trigram index (pg_trgm) — catches same story from different sources
- **Content fingerprint:** SimHash of article body — catches rephrased duplicates
- If duplicate found → link to existing article, skip remaining stages
- Similarity threshold configurable from admin dashboard

### Stage 3: Classify

- Assigns article to 1-3 of 11 categories via LLM (structured JSON output)
- Prompt includes category definitions and examples
- Falls back to keyword-based rules if LLM fails
- Uses local model (Ollama) by default to save API costs

**Categories:**

1. AI Models, Research & Benchmarks
2. AI Engineering & Developer Tools
3. Open Source AI Releases
4. AI Products & Features
5. AI Agents & Automation
6. AI Use Cases & Applications
7. AI Industry & Startups
8. AI Infrastructure & Big Tech
9. AI Policy, Safety & Governance
10. AI Security & Risks
11. Learning & Resources

### Stage 4: Score (Breaking News Detection)

Three-layer importance scoring:

| Layer | Range | Method |
|-------|-------|--------|
| Rule-based | 0-3 | Keyword hits (e.g., "GPT-5", "acquired", "vulnerability") |
| Source priority | 0-3 | Official announcements score higher than aggregator coverage |
| LLM score | 0-4 | "Rate this article's significance for AI professionals" — normalized |

**Combined score (0-10):**
- Score ≥ 8 → **BREAKING ALERT** — post immediately
- Score ≥ 5 → **HIGHLIGHT** — featured in daily digest
- Score < 5 → **NORMAL** — included in daily digest

### Stage 5: Summarize

- Generates 2-3 sentence summary of each article
- Also generates a one-line headline for Adaptive Cards
- Uses multi-LLM router (see next section)
- Prompt: "Summarize for a technical audience. Focus on what's new and why it matters."
- Stores both summary and headline in PostgreSQL

### Stage 6: Route

- **Breaking (≥8):** Immediately post to Teams as alert card
- **Digest (all):** Queue for next daily digest generation
- **Subscriber match:** Check user subscriptions — notify users subscribed to matching categories
- Digest job runs at configured time (e.g., 8:00 AM) via Celery Beat

## Multi-LLM Router

Distributes tasks across 4 providers with automatic fallback:

### Fallback Chain

```
Primary: OpenAI GPT
    ↓ fails/rate-limited
Fallback 1: Claude
    ↓ fails/rate-limited
Fallback 2: Gemini
    ↓ fails/rate-limited
Fallback 3: Ollama (local, always available)
```

### Task Routing Strategy

| Task | Primary Provider | Reason |
|------|-----------------|--------|
| Summarization | OpenAI | Primary provider, quality output |
| Classification | Ollama (local) | High volume, low complexity — saves API costs |
| Importance scoring | Rules + cheapest LLM | Split: rules/source handled locally, LLM score via cheapest available |
| Search queries | OpenAI or Claude | Best at understanding user intent |

### Cost Optimization

- Local model (Ollama) handles high-volume, low-complexity tasks
- Cloud LLMs reserved for quality-sensitive tasks
- Redis caches repeated requests — same article never summarized twice
- Per-provider rate limiter prevents hitting API limits

## Teams Bot

### Registration & Hosting

- Registered via Azure Bot Service (free tier)
- Bot Framework SDK for Python (`botbuilder-python`)
- Hosted inside FastAPI app at `/api/messages` endpoint

### Adaptive Card Formats

**Breaking News Alert:**
- Headline with alert indicator
- 2-3 sentence summary
- Category, source, importance score
- "Read Full Article" and "Related" action buttons

**Daily Digest:**
- Date and total article count
- Top stories section (highest scoring articles)
- Expandable category sections with article counts
- "View All", "Search", "Subscribe" action buttons

### Interactive Commands

| Command | Description |
|---------|-------------|
| `/latest` | 5 most recent articles |
| `/latest [category]` | Latest from a specific category |
| `/search [query]` | Full-text search across all articles |
| `/subscribe [category]` | Personal notifications for a category |
| `/unsubscribe [category]` | Remove subscription |
| `/digest now` | Generate an on-demand digest |
| `/summarize [URL]` | Scrape, summarize, classify, and store any article on demand |
| `/settings` | View/change preferences |
| `/help` | List all commands |

### User-Submitted Articles (/summarize)

When a user pastes a URL with `/summarize`:

1. Scrape the URL using the 5-layer scraping stack
2. Run through the full pipeline (ingest → dedup → classify → score → summarize)
3. Post summary back to the user as an Adaptive Card
4. Store in database — fully classified, searchable via `/search`
5. If already in database, return existing summary

## Admin Dashboard

FastAPI-served web UI at `http://localhost:8080/admin`. Built with FastAPI + Jinja2 templates + HTMX for interactivity. No auth — internal network trust.

### Pages

| Page | Content |
|------|---------|
| **Overview** | Total articles today, scrape success rate, LLM usage/costs, breaking alerts sent |
| **Sources** | All configured sources, scraper type, last scrape time, error count, enable/disable toggle |
| **Articles** | Searchable/filterable table — title, source, category, score, status |
| **Categories** | Manage 11 categories — add, edit, rename, view article counts |
| **Logs** | Real-time error logs — scrape failures, LLM errors, Teams posting failures |
| **Settings** | Digest schedule, scoring thresholds, LLM provider priority, scrape intervals |

## Data Model (PostgreSQL)

### sources

| Column | Type | Description |
|--------|------|-------------|
| id | PK | Auto-increment |
| name | varchar | Source display name |
| url | varchar | Source URL |
| scraper_type | enum | rss, api, firecrawl, playwright, readability |
| schedule_cron | varchar | Cron expression for scrape schedule |
| priority | int | Source priority for scoring (1-3) |
| enabled | boolean | Enable/disable toggle |
| last_scraped_at | timestamp | Last successful scrape |
| error_count | int | Consecutive error count |
| config_json | jsonb | Per-source config (API keys, selectors, etc.) |

### articles

| Column | Type | Description |
|--------|------|-------------|
| id | PK | Auto-increment |
| source_id | FK → sources | Which source this came from |
| title | varchar | Article title |
| url | varchar (unique) | Article URL |
| url_hash | varchar (indexed) | SHA-256 hash for fast dedup |
| raw_content | text | Original HTML/markdown |
| markdown_content | text | Cleaned markdown |
| author | varchar | Article author |
| published_at | timestamp | Original publish date |
| importance_score | int | Combined score 0-10 |
| is_breaking | boolean | Score ≥ 8 |
| is_user_submitted | boolean | From /summarize command |
| pipeline_status | enum | ingested, deduped, classified, scored, summarized, routed |
| created_at | timestamp | When we ingested it |

**Indexes:** url_hash (btree), title (pg_trgm for fuzzy search), published_at, importance_score, full-text index on markdown_content.

### categories

| Column | Type | Description |
|--------|------|-------------|
| id | PK | Auto-increment |
| name | varchar | Category name |
| description | text | Category definition (used in LLM prompts) |
| keywords | jsonb | Fallback keyword list |
| enabled | boolean | Enable/disable |

### article_categories (many-to-many)

| Column | Type | Description |
|--------|------|-------------|
| article_id | FK → articles | |
| category_id | FK → categories | |

### summaries

| Column | Type | Description |
|--------|------|-------------|
| id | PK | Auto-increment |
| article_id | FK → articles | |
| headline | varchar | One-line headline |
| summary_text | text | 2-3 sentence summary |
| llm_provider | varchar | Which LLM generated this |
| created_at | timestamp | |

### subscriptions

| Column | Type | Description |
|--------|------|-------------|
| id | PK | Auto-increment |
| teams_user_id | varchar | Teams user identifier |
| category_id | FK → categories | |
| created_at | timestamp | |

### post_logs

| Column | Type | Description |
|--------|------|-------------|
| id | PK | Auto-increment |
| article_id | FK → articles | |
| post_type | enum | alert, digest, user_request |
| teams_channel | varchar | Which channel/user |
| posted_at | timestamp | |
| status | enum | success, failed, pending |

## Technology Stack Summary

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Web Framework | FastAPI |
| Task Queue | Celery + Redis |
| Database | PostgreSQL with pg_trgm |
| Scraping | feedparser, requests, Firecrawl (self-hosted), Playwright, python-readability, markdownify |
| LLM Providers | OpenAI (primary), Claude, Gemini, Ollama (local) |
| Teams Integration | Bot Framework SDK (`botbuilder-python`) via Azure Bot Service |
| Dashboard | Jinja2 + HTMX |
| Deployment | Docker Compose (6 containers: app, celery-worker, postgres, redis, firecrawl, ollama) |

## Posting Strategy

- **Daily Digest:** Configurable time (default 8:00 AM), groups articles by category, highlights top stories
- **Breaking Alerts:** Immediate posting when importance score ≥ 8
- **Subscriber Notifications:** Personal notifications based on category subscriptions
- **Combination:** Daily digest for comprehensive coverage + real-time alerts for critical news
