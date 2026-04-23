# AI News Aggregator Bot

An AI-powered news aggregation system that continuously collects articles from
20+ sources (RSS feeds, APIs, web scraping), processes them through a
six-stage pipeline (ingest, dedup, classify, score, summarize, route), and
delivers curated news to Microsoft Teams via an interactive bot. Includes a
web-based admin dashboard for monitoring and management.

---

## Architecture

```
                           +--------------------+
                           |   Microsoft Teams   |
                           +--------+-----------+
                                    |
                           Bot Framework (webhook)
                                    |
+-------------------------------+   |   +-------------------------------+
|        Celery Workers         |   |   |       FastAPI Application      |
|                               |   |   |                               |
|  +----------+ +-----------+   |   |   |  /api/messages   (Bot)        |
|  | Scrape   | | Digest /  |   |   +-->|  /health         (Health)     |
|  | Tasks    | | Alert     |   |       |  /admin/*        (Dashboard)  |
|  +----+-----+ | Tasks     |   |       +-------+-----------+-----------+
|       |       +-----+-----+   |               |           |
|       v             v         |               v           v
|  +------------------------+   |    +----------+--+ +------+-------+
|  | Article Pipeline       |   |    | Dashboard   | | Bot Commands |
|  | 1. Ingest              |   |    | (HTMX +     | | /latest      |
|  | 2. Dedup (simhash)     |   |    |  Jinja2)    | | /search      |
|  | 3. Classify (LLM)      |   |    +-------------+ | /subscribe   |
|  | 4. Score               |   |                     | /digest ...  |
|  | 5. Summarize (LLM)     |   |                     +--------------+
|  | 6. Route (alert/digest)|   |
|  +----------+-------------+   |
+-------------|--+--------------+
              |  |
    +---------v--v--------+   +-----------+   +----------+   +---------+
    |    PostgreSQL 16     |   | Redis 7   |   | Firecrawl|   | Ollama  |
    | (articles, sources,  |   | (broker,  |   | (web     |   | (local  |
    |  categories, subs)   |   |  cache)   |   |  scrape) |   |  LLM)   |
    +----------------------+   +-----------+   +----------+   +---------+
```

### News Sources (18 pre-configured)

| Type      | Sources                                                                                |
|-----------|----------------------------------------------------------------------------------------|
| RSS       | TechCrunch, The Verge, Ars Technica, Wired, MIT Technology Review, VentureBeat        |
| API       | Hacker News, Reddit (r/MachineLearning, r/artificial, r/technology), arXiv, GitHub     |
| Firecrawl | OpenAI Blog, Anthropic Blog, Google DeepMind Blog, Meta AI Blog                       |

### Processing Pipeline

1. **Ingest** -- persist raw article, clean HTML to markdown, hash URL
2. **Dedup** -- URL-hash exact match + simhash-based title similarity
3. **Classify** -- assign 1-3 categories via LLM (keyword fallback)
4. **Score** -- importance score (1-10) from source priority, recency, content signals
5. **Summarize** -- generate headline + 2-3 sentence summary via LLM
6. **Route** -- breaking-news alerts (score >= threshold), digest queue, subscriber notifications

---

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- **Python 3.12+** (for local development outside Docker)
- **Azure Bot Registration** (for Teams integration)
  - Create a Bot Channel Registration in the Azure Portal
  - Note the App ID and App Password
  - Set the messaging endpoint to `https://<your-host>/api/messages`

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/your-org/AI-News-Aggregator-Bot.git
cd AI-News-Aggregator-Bot

# 2. Create your environment file
cp .env.example .env
# Edit .env and fill in your API keys (see Configuration below)

# 3. Start all services
docker compose up -d

# 4. Wait for containers to be healthy
docker compose ps          # all services should show "healthy" or "running"

# 5. Pull the local LLM model into Ollama
docker exec ollama ollama pull llama3.2:3b

# 6. Run database migrations
docker exec -it ai-news-aggregator-bot-app-1 alembic upgrade head

# 7. Seed categories and sources
docker exec -it ai-news-aggregator-bot-app-1 python -m src.config.seed_categories
docker exec -it ai-news-aggregator-bot-app-1 python -m src.config.seed_sources

# 8. Verify health
curl http://localhost:8080/health
```

The bot is now live. If Azure Bot Service is configured, it will respond to
commands in your Teams channel. The admin dashboard is at
`http://localhost:8080/admin/`.

---

## Configuration

All configuration is done through environment variables (`.env` file). The
table below describes every variable.

### Database and Cache

| Variable        | Default                                                     | Description                          |
|-----------------|-------------------------------------------------------------|--------------------------------------|
| `DATABASE_URL`  | `postgresql+asyncpg://postgres:postgres@localhost:5432/ainews` | PostgreSQL connection (asyncpg)     |
| `REDIS_URL`     | `redis://localhost:6379/0`                                  | Redis URL (Celery broker + LLM cache)|

### LLM API Keys

At least one provider should be configured. Ollama works without a key for
local-only setups.

| Variable            | Default | Description                        |
|---------------------|---------|------------------------------------|
| `OPENAI_API_KEY`    | (none)  | OpenAI API key                     |
| `ANTHROPIC_API_KEY` | (none)  | Anthropic (Claude) API key         |
| `GOOGLE_API_KEY`    | (none)  | Google AI (Gemini) API key         |

### Local Services

| Variable             | Default                    | Description                         |
|----------------------|----------------------------|-------------------------------------|
| `OLLAMA_BASE_URL`    | `http://localhost:11434`   | Ollama API base URL                 |
| `FIRECRAWL_BASE_URL` | `http://localhost:3002`    | Firecrawl service URL               |

### Azure Bot Service

| Variable                 | Default | Description                                          |
|--------------------------|---------|------------------------------------------------------|
| `AZURE_BOT_APP_ID`      | (none)  | Microsoft App ID from Azure Bot registration         |
| `AZURE_BOT_APP_PASSWORD`| (none)  | Microsoft App Password (client secret)               |
| `TEAMS_CHANNEL_ID`      | (none)  | Teams channel ID for proactive digest/alert messages |

### Scheduling and Thresholds

| Variable                    | Default | Description                                       |
|-----------------------------|---------|---------------------------------------------------|
| `DIGEST_SCHEDULE_HOUR`      | `8`     | Hour (UTC, 0-23) for daily digest delivery        |
| `DIGEST_SCHEDULE_MINUTE`    | `0`     | Minute (0-59) for daily digest delivery           |
| `BREAKING_NEWS_THRESHOLD`   | `8`     | Importance score (1-10) that triggers alerts      |

### Application

| Variable          | Default | Description                                     |
|-------------------|---------|-------------------------------------------------|
| `LOG_LEVEL`       | `INFO`  | Logging level: DEBUG, INFO, WARNING, ERROR      |
| `DASHBOARD_PORT`  | `8080`  | Port for the FastAPI application                |

---

## Bot Commands

All commands are available in Microsoft Teams by messaging the bot directly or
mentioning it in a channel.

| Command                   | Description                                                        |
|---------------------------|--------------------------------------------------------------------|
| `/latest`                 | Show the 5 most recent processed articles.                         |
| `/latest [category]`     | Show the 5 most recent articles in a specific category.            |
| `/search [query]`        | Full-text search across all articles (top 10 results by relevance).|
| `/subscribe [category]`  | Subscribe to alerts for a category. Run without args to list all.  |
| `/unsubscribe [category]`| Unsubscribe from a category. Run without args to see your subs.    |
| `/digest`                 | Generate an on-demand digest of articles from the last 24 hours.   |
| `/summarize [URL]`       | Summarize an article by URL (fetches and processes if not cached). |
| `/settings`               | View your notification preferences and subscriptions.              |
| `/settings key=value`    | Update a preference (e.g., `/settings digest_hour=9`).             |
| `/help`                   | Display the help card with all available commands.                 |

You can also paste a URL directly (without `/summarize`) and the bot will
attempt to summarize it.

### User Settings

Users can configure their preferences with `/settings key=value`:

| Key                    | Type | Default | Description                        |
|------------------------|------|---------|------------------------------------|
| `digest_enabled`       | bool | `true`  | Receive the daily digest           |
| `digest_hour`          | int  | `8`     | Preferred digest hour (UTC)        |
| `alert_enabled`        | bool | `true`  | Receive breaking-news alerts       |
| `min_importance_score` | int  | `5`     | Minimum score for alert delivery   |

---

## Admin Dashboard

The web-based admin dashboard is available at `http://localhost:8080/admin/`.

### Pages

| Page            | Path                 | Description                                        |
|-----------------|----------------------|----------------------------------------------------|
| Overview        | `/admin/`            | System stats: article counts, pipeline throughput  |
| Sources         | `/admin/sources`     | Manage news sources (enable/disable, view errors)  |
| Articles        | `/admin/articles`    | Browse all processed articles with filters         |
| Categories      | `/admin/categories`  | View and manage the 11 classification categories   |
| Post Logs       | `/admin/logs`        | History of all Teams posts (digests, alerts)        |
| Settings        | `/admin/settings`    | System-level configuration                         |

### REST API

The dashboard is backed by a JSON API under `/api/`:

- `GET /api/stats` -- aggregate statistics
- `GET/POST /api/sources` -- CRUD for news sources
- `GET /api/articles` -- paginated article listing
- `GET /api/categories` -- category management
- `GET /api/logs` -- post log history
- `GET/PUT /api/settings` -- system settings

---

## Health Check

The `/health` endpoint performs deep checks against all dependencies:

```json
{
  "status": "healthy",
  "checks": {
    "database": {"status": "ok", "latency_ms": 2.1},
    "redis": {"status": "ok", "latency_ms": 0.8},
    "firecrawl": {"status": "ok", "latency_ms": 15.3},
    "ollama": {"status": "ok", "latency_ms": 5.2, "models": ["llama3.2:3b"]}
  }
}
```

Status is `"healthy"` when all checks pass, or `"degraded"` when one or more
dependencies are unreachable. Docker Compose uses this endpoint for container
health checks.

---

## Development

### Local Setup (without Docker)

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[dev]"

# Install Playwright browser
playwright install chromium

# Run the application
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload

# Run the Celery worker (separate terminal)
celery -A src.config.celery_app worker --loglevel=info -B
```

### Running Tests

```bash
pytest tests/ -v --cov=src
```

### Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "description of changes"

# Apply all pending migrations
alembic upgrade head

# Roll back one migration
alembic downgrade -1
```

---

## Troubleshooting

### Bot not responding in Teams

1. Verify Azure Bot credentials: `AZURE_BOT_APP_ID` and `AZURE_BOT_APP_PASSWORD` must match your Azure Bot registration.
2. Ensure the messaging endpoint in Azure is set to `https://<your-host>/api/messages` (HTTPS required).
3. Check application logs: `docker compose logs app`.
4. Test the health endpoint: `curl http://localhost:8080/health`.

### No articles appearing

1. Confirm sources are seeded: `docker exec -it ai-news-aggregator-bot-app-1 python -m src.config.seed_sources`.
2. Check that the Celery worker is running: `docker compose logs celery-worker`.
3. Check Celery beat is scheduling tasks: look for "scrape-all-sources" in the worker logs.
4. Check individual source errors on the admin dashboard at `/admin/sources`.

### LLM calls failing

1. Verify at least one API key is set (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY`) or Ollama is running.
2. If using Ollama, ensure a model is pulled: `docker exec ollama ollama list`.
3. The LLM router falls back through providers automatically: OpenAI -> Claude -> Gemini -> Ollama.
4. Check Redis connectivity (used for LLM response caching): `docker exec redis redis-cli ping`.

### Firecrawl scraping not working

1. Confirm Firecrawl is running: `curl http://localhost:3002/`.
2. Firecrawl sources (OpenAI Blog, Anthropic Blog, etc.) require the Firecrawl container.
3. RSS and API sources work independently of Firecrawl.

### Database connection errors

1. Check Postgres is running: `docker compose ps postgres`.
2. Verify `DATABASE_URL` matches the Postgres container credentials.
3. Run migrations: `docker exec -it ai-news-aggregator-bot-app-1 alembic upgrade head`.

### Dashboard not loading

1. The dashboard is served at `/admin/` (note the trailing slash).
2. Static assets are served from `/admin/static/`.
3. Check the application logs for template rendering errors.

---

## Tech Stack

| Component          | Technology                                               |
|--------------------|----------------------------------------------------------|
| Web Framework      | FastAPI + Uvicorn                                        |
| Bot Framework      | Microsoft Bot Framework SDK (botbuilder-core)            |
| Database           | PostgreSQL 16 + SQLAlchemy 2 (async) + Alembic           |
| Task Queue         | Celery + Redis (broker + result backend)                 |
| LLM Providers      | OpenAI, Anthropic Claude, Google Gemini, Ollama (local)  |
| Web Scraping       | Firecrawl, Playwright, feedparser, httpx                 |
| Deduplication      | simhash (near-duplicate detection) + URL hashing         |
| Dashboard          | HTMX + Jinja2 templates + vanilla CSS/JS                 |
| Logging            | structlog (JSON in production, console in dev)           |
| Containerisation   | Docker + Docker Compose                                  |
| Language           | Python 3.12                                              |

---

## Project Structure

```
AI-News-Aggregator-Bot/
├── src/
│   ├── bot/                    # Microsoft Teams bot
│   │   ├── adapter.py          # Bot Framework adapter + FastAPI route
│   │   ├── bot_app.py          # ActivityHandler (message routing)
│   │   ├── cards/              # Adaptive Card templates
│   │   ├── commands/           # Command handlers (/latest, /search, ...)
│   │   └── services/           # Posting service (Teams proactive messages)
│   ├── config/                 # Application configuration
│   │   ├── settings.py         # Pydantic settings (env vars)
│   │   ├── celery_app.py       # Celery + beat schedule
│   │   ├── logging.py          # Structured logging setup
│   │   ├── health.py           # Dependency health checks
│   │   ├── seed_categories.py  # Category seed data
│   │   ├── seed_sources.py     # Source seed data
│   │   ├── scoring_rules.py    # Importance scoring rules
│   │   └── classification_prompts.py
│   ├── dashboard/              # Admin web UI
│   │   ├── routes.py           # HTMX page routes
│   │   ├── templates/          # Jinja2 templates
│   │   ├── static/             # CSS, JS, images
│   │   └── api/                # REST API endpoints
│   ├── llm/                    # LLM provider abstraction
│   │   ├── router.py           # Multi-provider router with fallback
│   │   ├── openai_provider.py
│   │   ├── claude_provider.py
│   │   ├── gemini_provider.py
│   │   └── ollama_provider.py
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── article.py
│   │   ├── category.py
│   │   ├── source.py
│   │   ├── subscription.py
│   │   ├── summary.py
│   │   └── post_log.py
│   ├── pipeline/               # Six-stage article processing
│   │   ├── orchestrator.py     # Pipeline runner
│   │   ├── ingest.py
│   │   ├── dedup.py
│   │   ├── classify.py
│   │   ├── score.py
│   │   ├── summarize.py
│   │   └── route.py
│   ├── scrapers/               # Data collection
│   │   ├── registry.py         # Scraper type registry
│   │   ├── rss_scraper.py
│   │   ├── hn_scraper.py
│   │   ├── reddit_scraper.py
│   │   ├── arxiv_scraper.py
│   │   ├── github_scraper.py
│   │   ├── firecrawl_scraper.py
│   │   ├── playwright_scraper.py
│   │   └── content_cleaner.py
│   ├── scheduler/              # Celery tasks
│   │   ├── scrape_tasks.py
│   │   ├── digest_tasks.py
│   │   └── alert_tasks.py
│   └── main.py                 # FastAPI app entrypoint
├── alembic/                    # Database migrations
├── docker/                     # Docker support files
│   └── postgres/init.sql
├── tests/                      # Test suite
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```
