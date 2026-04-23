# AI News Aggregator Bot — Implementation Plan

**Spec:** [2026-04-23-ai-news-aggregator-bot-design.md](./2026-04-23-ai-news-aggregator-bot-design.md)
**Approach:** Subagent-driven development — each step is an independent, reviewable unit.
**Review gates:** After each phase, run code-reviewer and architecture-reviewer agents before proceeding.

---

## Phase 1: Project Foundation & Infrastructure

> Goal: Working Docker Compose stack with database, config, and project structure.

### Step 1.1: Project scaffolding
- **What:** Create Python project structure with `pyproject.toml`, directory layout, `.gitignore`, `.env.example`
- **Directory structure:**
  ```
  src/
    scrapers/          # Layer 1-5 scraper adapters
    pipeline/          # 6-stage article processing
    llm/               # Multi-LLM router
    bot/               # Teams Bot Framework
    dashboard/         # Admin web UI
    models/            # SQLAlchemy models
    config/            # Settings, constants
    utils/             # Shared utilities
  tests/
    unit/
    integration/
  docker/
  docs/
  ```
- **Files:** `pyproject.toml`, `src/__init__.py`, `src/config/settings.py` (Pydantic settings from env vars)
- **Review criteria:** Clean structure, all modules importable, settings load from `.env`

### Step 1.2: Docker Compose setup
- **What:** Create `docker-compose.yml` with all 6 containers
- **Containers:** `app`, `celery-worker`, `postgres`, `redis`, `firecrawl`, `ollama`
- **Files:** `docker-compose.yml`, `Dockerfile`, `docker/postgres/init.sql`, `docker/ollama/pull-model.sh`
- **Acceptance:** `docker compose up` starts all 6 containers, all healthy, inter-container networking works
- **Review criteria:** No hardcoded secrets, volumes for persistence, health checks on all containers

### Step 1.3: Database schema & migrations
- **What:** SQLAlchemy models + Alembic migrations for all 7 tables
- **Tables:** `sources`, `articles`, `categories`, `article_categories`, `summaries`, `subscriptions`, `post_logs`
- **Indexes:** url_hash (btree), title (pg_trgm), published_at, importance_score, full-text on markdown_content
- **Files:** `src/models/*.py`, `alembic/`, `alembic.ini`
- **Acceptance:** `alembic upgrade head` creates all tables, indexes, constraints. `alembic downgrade base` cleanly reverses.
- **Review criteria:** FK constraints, enum types, pg_trgm extension enabled, no missing indexes from spec

### Step 1.4: Seed data — categories
- **What:** Seed script to insert the 11 categories with descriptions and keyword lists
- **Files:** `src/config/seed_categories.py`
- **Acceptance:** Running seed populates all 11 categories, idempotent (safe to re-run)
- **Review criteria:** Category descriptions are clear enough for LLM classification prompts

### 🔍 REVIEW GATE: Phase 1
- **Code reviewer:** Verify project structure, Docker setup, DB schema matches spec
- **Architecture reviewer:** Confirm module boundaries, config approach, migration strategy

---

## Phase 2: Scraping Layer

> Goal: All 5 scraper layers working, producing clean markdown from real sources.

### Step 2.1: Scraper base interface
- **What:** Abstract base class `BaseScraper` with common interface: `scrape() -> list[RawArticle]`
- **Data class:** `RawArticle(title, url, raw_content, source_name, published_at, author, metadata)`
- **Files:** `src/scrapers/base.py`
- **Review criteria:** Clean interface, type hints, docstrings on abstract methods

### Step 2.2: RSS/Atom scraper (Layer 1)
- **What:** `RssScraper(BaseScraper)` using `feedparser`
- **Handles:** Feed parsing, entry normalization, date parsing, error handling for malformed feeds
- **Config:** Feed URL from `sources.config_json`
- **Files:** `src/scrapers/rss_scraper.py`, `tests/unit/test_rss_scraper.py`
- **Acceptance:** Successfully scrapes TechCrunch, The Verge, Ars Technica RSS feeds. Returns normalized `RawArticle` list.
- **Review criteria:** Graceful handling of malformed feeds, timeout handling, proper date parsing

### Step 2.3: API scrapers (Layer 2)
- **What:** Individual scrapers for each API source
- **Sub-tasks:**
  - `GitHubScraper` — trending repos + release feeds via GitHub API
  - `RedditScraper` — top posts from r/MachineLearning, r/artificial, r/technology via Reddit API
  - `HackerNewsScraper` — top/new stories via Firebase HN API
  - `ArxivScraper` — AI/ML papers via arXiv API
- **Files:** `src/scrapers/github_scraper.py`, `src/scrapers/reddit_scraper.py`, `src/scrapers/hn_scraper.py`, `src/scrapers/arxiv_scraper.py`, `tests/unit/test_api_scrapers.py`
- **Acceptance:** Each scraper returns `RawArticle` list from real API calls. Rate limits respected.
- **Review criteria:** API key handling via env vars, pagination support, rate limit headers respected, error handling

### Step 2.4: Firecrawl scraper (Layer 3)
- **What:** `FirecrawlScraper(BaseScraper)` connecting to self-hosted Firecrawl instance
- **Handles:** URL submission, markdown retrieval, JS-rendered pages
- **Config:** Firecrawl host URL from env var
- **Files:** `src/scrapers/firecrawl_scraper.py`, `tests/unit/test_firecrawl_scraper.py`
- **Acceptance:** Scrapes OpenAI blog, Anthropic blog via Firecrawl. Returns clean markdown.
- **Review criteria:** Connection error handling, timeout config, retry logic

### Step 2.5: Playwright scraper (Layer 4)
- **What:** `PlaywrightScraper(BaseScraper)` for JS-heavy sites
- **Handles:** Headless browser launch, page navigation, content extraction, cookie wall dismissal
- **Resource management:** Browser context pooling, proper cleanup, memory limits
- **Files:** `src/scrapers/playwright_scraper.py`, `tests/unit/test_playwright_scraper.py`
- **Acceptance:** Scrapes a JS-heavy test site, extracts content, cleans up browser resources.
- **Review criteria:** Memory management, browser context reuse, timeout handling, graceful shutdown

### Step 2.6: Content cleaner (Layer 5 — post-processor)
- **What:** `ContentCleaner` using `python-readability` + `markdownify`
- **Handles:** Strip ads/nav/boilerplate from HTML, convert to clean markdown
- **Applied after:** Layers 1, 2, and 4 (Firecrawl already outputs markdown)
- **Files:** `src/scrapers/content_cleaner.py`, `tests/unit/test_content_cleaner.py`
- **Acceptance:** Given messy news HTML, outputs clean markdown with title, body, metadata preserved.
- **Review criteria:** Handles edge cases (empty body, missing title, non-English chars)

### Step 2.7: Scraper registry & factory
- **What:** Registry that maps `scraper_type` enum to scraper class. Factory creates scraper from `sources` DB row.
- **Auto-selection:** Given a URL, detect best scraper layer (RSS check → API check → Firecrawl → Playwright)
- **Files:** `src/scrapers/registry.py`, `tests/unit/test_scraper_registry.py`
- **Acceptance:** `ScraperRegistry.get("rss")` returns `RssScraper`. `ScraperRegistry.auto_detect(url)` picks the right layer.
- **Review criteria:** Extensible for new scraper types, no circular imports

### 🔍 REVIEW GATE: Phase 2
- **Code reviewer:** Each scraper works independently, clean error handling, tests pass
- **Architecture reviewer:** Scraper interface consistency, factory pattern, content cleaner integration

---

## Phase 3: Multi-LLM Router

> Goal: Unified interface to call any LLM with automatic fallback and cost routing.

### Step 3.1: LLM provider base interface
- **What:** Abstract `BaseLLMProvider` with `generate(prompt, system_prompt, json_mode) -> LLMResponse`
- **Data class:** `LLMResponse(text, provider, model, tokens_used, latency_ms, cost_estimate)`
- **Files:** `src/llm/base.py`
- **Review criteria:** Clean async interface, structured response, error types defined

### Step 3.2: OpenAI provider
- **What:** `OpenAIProvider(BaseLLMProvider)` using `openai` SDK
- **Handles:** Chat completion, JSON mode, token counting, rate limit detection
- **Files:** `src/llm/openai_provider.py`, `tests/unit/test_openai_provider.py`
- **Acceptance:** Successfully calls OpenAI API, returns structured `LLMResponse`, handles rate limits gracefully.
- **Review criteria:** API key from env, async calls, proper error handling, token tracking

### Step 3.3: Claude provider
- **What:** `ClaudeProvider(BaseLLMProvider)` using `anthropic` SDK
- **Files:** `src/llm/claude_provider.py`, `tests/unit/test_claude_provider.py`
- **Acceptance:** Successfully calls Claude API, structured response, fallback-ready.
- **Review criteria:** Same as OpenAI — env key, async, error handling, token tracking

### Step 3.4: Gemini provider
- **What:** `GeminiProvider(BaseLLMProvider)` using `google-generativeai` SDK
- **Files:** `src/llm/gemini_provider.py`, `tests/unit/test_gemini_provider.py`
- **Acceptance:** Successfully calls Gemini API, structured response, fallback-ready.
- **Review criteria:** Same as above

### Step 3.5: Ollama provider (local)
- **What:** `OllamaProvider(BaseLLMProvider)` connecting to Ollama Docker container
- **Handles:** Local model inference, no API key needed, always-available fallback
- **Files:** `src/llm/ollama_provider.py`, `tests/unit/test_ollama_provider.py`
- **Acceptance:** Calls local Ollama instance, returns response. Works with Llama/Mistral models.
- **Review criteria:** Connection to Docker container, model availability check, timeout for slow local inference

### Step 3.6: LLM router
- **What:** `LLMRouter` that implements fallback chain and task-based routing
- **Fallback chain:** OpenAI → Claude → Gemini → Ollama
- **Task routing:** Summarization → OpenAI, Classification → Ollama, Scoring → cheapest, Search → OpenAI/Claude
- **Features:** Per-provider rate limiting, Redis caching of responses, cost tracking
- **Files:** `src/llm/router.py`, `tests/unit/test_llm_router.py`
- **Acceptance:** Routes tasks to correct provider. Falls back on failure. Caches duplicate requests. Tracks costs.
- **Review criteria:** No single point of failure, cache invalidation strategy, rate limit implementation

### 🔍 REVIEW GATE: Phase 3
- **Code reviewer:** All 4 providers work, router fallback logic correct, caching works
- **Architecture reviewer:** Provider interface consistency, routing strategy, cost optimization approach

---

## Phase 4: Article Pipeline

> Goal: Complete 6-stage processing pipeline that takes raw articles and produces scored, summarized, categorized output.

### Step 4.1: Ingest stage
- **What:** `IngestStage` — normalize `RawArticle` to DB `Article`, apply content cleaner, store in PostgreSQL
- **Files:** `src/pipeline/ingest.py`, `tests/unit/test_ingest.py`
- **Acceptance:** Raw article in → normalized article stored in DB with markdown content, metadata extracted.
- **Review criteria:** Handles missing fields gracefully, idempotent (same URL doesn't create duplicates)

### Step 4.2: Dedup stage
- **What:** `DedupStage` — URL hash check, title fuzzy match (pg_trgm), SimHash content fingerprint
- **Files:** `src/pipeline/dedup.py`, `src/utils/simhash.py`, `tests/unit/test_dedup.py`
- **Acceptance:** Exact URL dups caught. Similar titles (>0.8 similarity) caught. Rephrased content caught. Threshold configurable.
- **Review criteria:** pg_trgm query performance, SimHash accuracy, configurable thresholds

### Step 4.3: Classify stage
- **What:** `ClassifyStage` — assign 1-3 categories via LLM (JSON mode), keyword fallback
- **LLM prompt:** Includes all 11 category names + descriptions + few-shot examples
- **Routing:** Uses Ollama (local) by default for cost savings
- **Files:** `src/pipeline/classify.py`, `src/config/classification_prompts.py`, `tests/unit/test_classify.py`
- **Acceptance:** Articles classified into correct categories. Keyword fallback works when LLM unavailable.
- **Review criteria:** Prompt engineering quality, JSON output parsing, fallback robustness

### Step 4.4: Score stage
- **What:** `ScoreStage` — three-layer importance scoring (rules + source priority + LLM)
- **Rule engine:** Configurable keyword lists with weights
- **Source priority:** Read from `sources.priority` field (1-3)
- **LLM scoring:** "Rate significance 1-10" → normalized to 0-4
- **Combined:** Sum to 0-10, set `is_breaking` flag if ≥ 8
- **Files:** `src/pipeline/score.py`, `src/config/scoring_rules.py`, `tests/unit/test_score.py`
- **Acceptance:** Articles scored correctly. Breaking threshold works. Rules configurable.
- **Review criteria:** Scoring formula consistency with spec, rule engine extensibility

### Step 4.5: Summarize stage
- **What:** `SummarizeStage` — generate 2-3 sentence summary + one-line headline via LLM router
- **Routing:** Uses OpenAI (primary) via LLM router
- **Caching:** Redis cache keyed by article URL hash — never summarize twice
- **Files:** `src/pipeline/summarize.py`, `tests/unit/test_summarize.py`
- **Acceptance:** Articles get quality summaries. Cache prevents re-processing. Headline is concise.
- **Review criteria:** Prompt quality, cache key strategy, summary length enforcement

### Step 4.6: Route stage
- **What:** `RouteStage` — decide alert vs digest, check subscriber matches, queue for posting
- **Breaking (≥8):** Push to Celery task for immediate Teams alert
- **All articles:** Add to digest queue (Redis sorted set by score)
- **Subscriber check:** Query `subscriptions` table, push personal notifications
- **Files:** `src/pipeline/route.py`, `tests/unit/test_route.py`
- **Acceptance:** Breaking articles trigger immediate task. All articles queued for digest. Subscribers notified.
- **Review criteria:** No articles lost, subscriber matching efficiency, Celery task creation

### Step 4.7: Pipeline orchestrator
- **What:** `ArticlePipeline` — chains all 6 stages, handles errors at each stage, updates `pipeline_status`
- **Error handling:** If a stage fails, article stays at previous status, error logged, pipeline continues with next article
- **Files:** `src/pipeline/orchestrator.py`, `tests/integration/test_pipeline_e2e.py`
- **Acceptance:** Full end-to-end: raw article → stored, deduped, classified, scored, summarized, routed. Pipeline status tracked.
- **Review criteria:** Error isolation (one article failure doesn't block others), status tracking, logging

### 🔍 REVIEW GATE: Phase 4
- **Code reviewer:** All 6 stages work independently and chained, error handling robust, tests pass
- **Architecture reviewer:** Pipeline pattern, stage independence, error isolation, status tracking

---

## Phase 5: Celery Scheduler

> Goal: Automated scraping on schedules, digest generation, breaking news checks.

### Step 5.1: Celery configuration
- **What:** Celery app setup with Redis broker, result backend, serialization config
- **Files:** `src/config/celery_app.py`
- **Acceptance:** Celery worker starts, connects to Redis, can execute test tasks.
- **Review criteria:** Serialization format, retry policy, concurrency settings

### Step 5.2: Scraping tasks
- **What:** Celery tasks that trigger scrapers based on source schedules
- **Tasks:** `scrape_source(source_id)`, `scrape_all_sources()`
- **Scheduling:** Celery Beat reads cron expressions from `sources.schedule_cron`
- **Files:** `src/scheduler/scrape_tasks.py`, `tests/unit/test_scrape_tasks.py`
- **Acceptance:** Sources scraped on schedule. New articles flow through pipeline. Errors logged, `error_count` incremented.
- **Review criteria:** Task idempotency, error handling, schedule loading from DB

### Step 5.3: Digest generation task
- **What:** Celery task that collects queued articles, groups by category, generates digest
- **Task:** `generate_daily_digest()` — runs at configured time (default 8:00 AM)
- **Output:** Structured digest object ready for Adaptive Card rendering
- **Files:** `src/scheduler/digest_tasks.py`, `tests/unit/test_digest_tasks.py`
- **Acceptance:** Digest generated with top stories + category sections. Only includes articles since last digest.
- **Review criteria:** Time window handling, category grouping, empty digest handling

### Step 5.4: Breaking news task
- **What:** Celery task triggered by route stage when article scores ≥ 8
- **Task:** `post_breaking_alert(article_id)` — formats and posts immediately
- **Files:** `src/scheduler/alert_tasks.py`, `tests/unit/test_alert_tasks.py`
- **Acceptance:** Breaking article → Teams alert posted within seconds. Post logged in `post_logs`.
- **Review criteria:** Fast execution, duplicate alert prevention, logging

### 🔍 REVIEW GATE: Phase 5
- **Code reviewer:** Tasks work correctly, scheduling reliable, error handling robust
- **Architecture reviewer:** Celery configuration, task design, schedule management approach

---

## Phase 6: Teams Bot Integration

> Goal: Full Bot Framework bot with Adaptive Cards and all interactive commands.

### Step 6.1: Bot Framework setup
- **What:** Azure Bot Service registration, `botbuilder-python` integration with FastAPI
- **Endpoint:** `/api/messages` in FastAPI
- **Files:** `src/bot/bot_app.py`, `src/bot/adapter.py`
- **Acceptance:** Bot registered in Azure, responds to messages in Teams channel.
- **Review criteria:** App ID/password from env vars, proper adapter configuration

### Step 6.2: Adaptive Card templates
- **What:** Card templates for all message types
- **Templates:**
  - Breaking news alert card
  - Daily digest card (with expandable category sections)
  - Article detail card (for `/latest`, `/search` results)
  - Help card
  - Settings card
- **Files:** `src/bot/cards/alert_card.py`, `src/bot/cards/digest_card.py`, `src/bot/cards/article_card.py`, `src/bot/cards/help_card.py`, `src/bot/cards/settings_card.py`
- **Acceptance:** All cards render correctly in Teams with proper formatting, buttons, expandable sections.
- **Review criteria:** Card schema validation, responsive layout, action buttons functional

### Step 6.3: Command handler — /latest
- **What:** Returns 5 most recent articles, optionally filtered by category
- **Usage:** `/latest` or `/latest AI Models`
- **Files:** `src/bot/commands/latest.py`, `tests/unit/test_cmd_latest.py`
- **Acceptance:** Returns correct articles as Adaptive Cards. Category filter works.
- **Review criteria:** Query efficiency, category name matching (fuzzy), empty result handling

### Step 6.4: Command handler — /search
- **What:** Full-text search across all articles using PostgreSQL full-text index
- **Usage:** `/search transformer architecture`
- **Files:** `src/bot/commands/search.py`, `tests/unit/test_cmd_search.py`
- **Acceptance:** Returns relevant results ranked by relevance. Highlights matching terms.
- **Review criteria:** Search query sanitization (SQL injection prevention), pagination, relevance ranking

### Step 6.5: Command handler — /subscribe & /unsubscribe
- **What:** Manage personal category subscriptions
- **Usage:** `/subscribe AI Agents & Automation`, `/unsubscribe AI Security`
- **Files:** `src/bot/commands/subscribe.py`, `tests/unit/test_cmd_subscribe.py`
- **Acceptance:** Subscription stored in DB. Confirmation card sent. Unsubscribe removes it.
- **Review criteria:** Category name fuzzy matching, duplicate subscription handling, confirmation feedback

### Step 6.6: Command handler — /digest now
- **What:** Generate and send an on-demand digest for the requesting user
- **Files:** `src/bot/commands/digest.py`, `tests/unit/test_cmd_digest.py`
- **Acceptance:** Digest generated with articles since last digest, sent as Adaptive Card.
- **Review criteria:** Doesn't interfere with scheduled digest, proper time window

### Step 6.7: Command handler — /summarize [URL]
- **What:** User submits a URL, bot scrapes → processes → returns summary
- **Usage:** `/summarize https://openai.com/blog/gpt-5`
- **Flow:** Detect scraper → scrape → pipeline (ingest→dedup→classify→score→summarize) → post card → store
- **Files:** `src/bot/commands/summarize.py`, `tests/unit/test_cmd_summarize.py`
- **Acceptance:** URL scraped, summarized, stored in DB, summary card sent to user. Existing articles return cached summary.
- **Review criteria:** URL validation, timeout handling, user feedback during processing (typing indicator)

### Step 6.8: Command handler — /settings & /help
- **What:** User preferences and command documentation
- **Settings:** Digest time preference, notification frequency, category visibility
- **Files:** `src/bot/commands/settings.py`, `src/bot/commands/help.py`, `tests/unit/test_cmd_settings.py`
- **Acceptance:** Settings card renders with current values. Changes persist. Help lists all commands.
- **Review criteria:** Settings persistence, input validation, clear help text

### Step 6.9: Digest posting service
- **What:** Service that formats digest and posts to Teams channel via Bot Framework
- **Called by:** Celery digest task (Step 5.3)
- **Files:** `src/bot/services/posting_service.py`, `tests/unit/test_posting_service.py`
- **Acceptance:** Digest Adaptive Card posted to configured channel. Post logged in `post_logs`.
- **Review criteria:** Channel configuration, error handling, retry on Teams API failure

### 🔍 REVIEW GATE: Phase 6
- **Code reviewer:** All commands work, cards render correctly, posting reliable
- **Architecture reviewer:** Bot command pattern, card template approach, posting service design

---

## Phase 7: Admin Dashboard

> Goal: Web UI for monitoring and configuring the bot.

### Step 7.1: Dashboard base setup
- **What:** FastAPI routes for dashboard at `/admin`, Jinja2 templates, HTMX integration, static assets
- **Files:** `src/dashboard/routes.py`, `src/dashboard/templates/base.html`, `src/dashboard/static/`
- **Acceptance:** Dashboard accessible at `http://localhost:8080/admin`, base layout renders.
- **Review criteria:** Template inheritance, static file serving, HTMX loaded

### Step 7.2: Overview page
- **What:** Dashboard home — total articles today, scrape success rate, LLM usage/costs, breaking alerts sent
- **Data:** Real-time stats from DB queries
- **Files:** `src/dashboard/templates/overview.html`, `src/dashboard/api/stats.py`
- **Acceptance:** Stats display correctly, auto-refresh via HTMX polling.
- **Review criteria:** Query performance, stat calculation accuracy

### Step 7.3: Sources management page
- **What:** List all sources, show scraper type, last scrape, error count, enable/disable toggle
- **Actions:** Add source, edit source config, enable/disable, trigger manual scrape
- **Files:** `src/dashboard/templates/sources.html`, `src/dashboard/api/sources.py`
- **Acceptance:** Sources CRUD works. Toggle enable/disable updates DB. Manual scrape triggers Celery task.
- **Review criteria:** Form validation, HTMX partial updates, error feedback

### Step 7.4: Articles browser page
- **What:** Searchable, filterable, sortable table of all articles
- **Filters:** Category, source, score range, date range, pipeline status
- **Files:** `src/dashboard/templates/articles.html`, `src/dashboard/api/articles.py`
- **Acceptance:** Search works, filters apply, pagination works, click to view article detail.
- **Review criteria:** Query performance with filters, pagination implementation, XSS prevention in search

### Step 7.5: Categories management page
- **What:** CRUD for categories — add, edit, rename, view article counts, manage keywords
- **Files:** `src/dashboard/templates/categories.html`, `src/dashboard/api/categories.py`
- **Acceptance:** Categories editable. Keyword lists manageable. Article counts accurate.
- **Review criteria:** Validation, keyword format, impact on existing classifications

### Step 7.6: Logs page
- **What:** Real-time error logs — scrape failures, LLM errors, Teams posting failures
- **Implementation:** Tail structured log file via HTMX streaming, or poll log entries from DB
- **Files:** `src/dashboard/templates/logs.html`, `src/dashboard/api/logs.py`
- **Acceptance:** Errors appear in real-time. Filterable by log level and component.
- **Review criteria:** Log volume handling, no sensitive data in logs, filter performance

### Step 7.7: Settings page
- **What:** Global settings — digest schedule, scoring thresholds, LLM priority, scrape intervals
- **Files:** `src/dashboard/templates/settings.html`, `src/dashboard/api/settings.py`
- **Acceptance:** Settings saved and applied. Changes to digest time update Celery Beat schedule.
- **Review criteria:** Validation, immediate vs restart-required settings, cron expression validation

### 🔍 REVIEW GATE: Phase 7
- **Code reviewer:** All pages work, CRUD operations correct, no XSS/injection vulnerabilities
- **Architecture reviewer:** Dashboard routing, API layer, template structure, HTMX patterns

---

## Phase 8: Integration & End-to-End Testing

> Goal: Everything works together. Full flow from scrape to Teams post.

### Step 8.1: Integration test — scraping to pipeline
- **What:** Test that scrapers feed into pipeline correctly for each scraper type
- **Files:** `tests/integration/test_scraper_pipeline.py`
- **Acceptance:** RSS article → pipeline → stored, classified, scored, summarized in DB.

### Step 8.2: Integration test — pipeline to Teams
- **What:** Test that pipeline output triggers correct Teams actions (alerts, digest queue)
- **Files:** `tests/integration/test_pipeline_teams.py`
- **Acceptance:** Breaking article → alert card posted. Digest articles queued and posted on schedule.

### Step 8.3: Integration test — bot commands
- **What:** Test all 8 bot commands end-to-end with real DB data
- **Files:** `tests/integration/test_bot_commands.py`
- **Acceptance:** All commands return correct responses with real data.

### Step 8.4: Integration test — /summarize flow
- **What:** User submits URL → scrape → pipeline → summary card → stored in DB
- **Files:** `tests/integration/test_summarize_flow.py`
- **Acceptance:** Full flow works. Duplicate URL returns cached result.

### Step 8.5: Load & resilience testing
- **What:** Test with high article volume, LLM failures, network errors
- **Scenarios:** 100+ articles in batch, all LLMs down (Ollama fallback), Postgres connection loss, Teams API down
- **Files:** `tests/integration/test_resilience.py`
- **Acceptance:** System degrades gracefully. No data loss. Errors logged. Recovery works.

### 🔍 REVIEW GATE: Phase 8
- **Code reviewer:** All integration tests pass, error scenarios covered
- **Architecture reviewer:** Test coverage adequate, resilience patterns correct

---

## Phase 9: Production Readiness

> Goal: Logging, monitoring, documentation, ready to run.

### Step 9.1: Structured logging
- **What:** Configure `structlog` across all modules — JSON format, correlation IDs, log levels
- **Files:** `src/config/logging.py`, update all modules
- **Acceptance:** All actions logged with context. Logs parseable. Correlation ID tracks article through pipeline.

### Step 9.2: Source configuration
- **What:** Populate `sources` table with all initial news sources, correct scraper types, schedules
- **Sources list:**
  - RSS: TechCrunch, The Verge, Ars Technica, Wired, MIT Tech Review, VentureBeat
  - API: GitHub (trending + releases), Reddit (3 subreddits), HN, arXiv
  - Firecrawl: OpenAI blog, Anthropic blog, Google DeepMind blog, Meta AI blog
- **Files:** `src/config/seed_sources.py`
- **Acceptance:** All sources seeded with correct config. First scrape succeeds for each.

### Step 9.3: Environment documentation
- **What:** Complete `.env.example` with all required variables, `README.md` with setup instructions
- **Variables:** DB connection, Redis URL, LLM API keys, Azure Bot credentials, Firecrawl URL, digest time
- **Files:** `.env.example`, `README.md`
- **Acceptance:** New developer can clone repo, copy `.env.example`, fill in keys, `docker compose up`, bot works.

### Step 9.4: Health checks & monitoring
- **What:** `/health` endpoint checking all dependencies (DB, Redis, Firecrawl, Ollama, Teams)
- **Files:** `src/config/health.py`
- **Acceptance:** Health endpoint returns status of each dependency. Dashboard overview uses same checks.

### 🔍 FINAL REVIEW GATE
- **Code reviewer:** Full codebase review — code quality, security, error handling, test coverage
- **Architecture reviewer:** Module boundaries clean, no circular deps, scaling path clear, spec compliance

---

## Phase Summary

| Phase | Steps | Description |
|-------|-------|-------------|
| 1 | 1.1–1.4 | Project foundation, Docker, DB schema |
| 2 | 2.1–2.7 | 5-layer scraping stack |
| 3 | 3.1–3.6 | Multi-LLM router with fallback |
| 4 | 4.1–4.7 | 6-stage article pipeline |
| 5 | 5.1–5.4 | Celery scheduler & tasks |
| 6 | 6.1–6.9 | Teams Bot & commands |
| 7 | 7.1–7.7 | Admin dashboard |
| 8 | 8.1–8.5 | Integration & E2E testing |
| 9 | 9.1–9.4 | Production readiness |

**Total: 9 phases, 48 steps, 9 review gates**

## Subagent Execution Strategy

Each step is designed to be executed by an independent code agent:

- **Independence:** Each step has clear inputs, outputs, files, and acceptance criteria
- **Parallelism within phases:** Steps within a phase can often run in parallel (e.g., all 4 LLM providers in Phase 3)
- **Sequential across phases:** Phases must complete in order (Phase 2 depends on Phase 1, etc.)
- **Review gates:** After each phase, code-reviewer and architecture-reviewer agents validate before proceeding
- **Isolation:** Each step works in its own files — no two steps edit the same file simultaneously
