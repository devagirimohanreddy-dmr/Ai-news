# Deployment Guide — AI-News Aggregator

> Production deployment instructions for the AI-News Aggregator backend.
> This guide is written for a deployment team unfamiliar with the project.

**Document version:** 1.0
**Last updated:** 14 May 2026
**Estimated deployment time:** 45–60 minutes

---

## Table of Contents

1. [What you're deploying](#1-what-youre-deploying)
2. [Prerequisites](#2-prerequisites)
3. [Pre-deployment checklist](#3-pre-deployment-checklist)
4. [Step-by-step deployment](#4-step-by-step-deployment)
5. [Make the server publicly reachable](#5-make-the-server-publicly-reachable)
6. [Wire Microsoft Teams integration](#6-wire-microsoft-teams-integration)
7. [Verify end-to-end](#7-verify-end-to-end)
8. [Keep it running after reboot](#8-keep-it-running-after-reboot)
9. [Routine maintenance](#9-routine-maintenance)
10. [Troubleshooting](#10-troubleshooting)
11. [Reference](#11-reference)

---

## 1. What you're deploying

**AI-News Aggregator** — an automated news aggregation platform written in Python. The backend:

- Scrapes ~18 news sources continuously on individually-tuned schedules
- Resolves Google News redirect URLs to the underlying publisher articles
- Extracts article body content and hero images
- Summarizes each article using a local LLM (Ollama)
- Exposes an admin dashboard for browsing and configuration
- Exposes a token-protected RSS feed that **Microsoft Power Automate polls** to push curated articles into a Microsoft Teams channel as Adaptive Cards

The entire system is **containerized via Docker Compose**. Deployment means running 5 containers on a single Linux VM.

### Architecture summary

```
                External news sources (RSS, HN, Reddit, etc.)
                                │
                                ▼
              ┌──────────────────────────────────────────┐
              │   Docker Compose Stack (5 containers)    │
              │                                          │
              │   ┌──────────┐    ┌─────────────────┐   │
              │   │  app     │    │  celery-worker  │   │
              │   │ FastAPI  │    │ scheduler+jobs  │   │
              │   └────┬─────┘    └────────┬────────┘   │
              │        │                   │            │
              │        ▼                   ▼            │
              │   ┌──────────┐    ┌─────────────┐      │
              │   │postgres  │    │   redis     │      │
              │   └──────────┘    └─────────────┘      │
              │                                          │
              │             ┌──────────┐                 │
              │             │  ollama  │ (local LLM)     │
              │             └──────────┘                 │
              └──────────────┬───────────────────────────┘
                             │
                             ▼  (HTTP / port 8080)
              ┌──────────────────────────────────────────┐
              │ Public URL exposed via reverse proxy or  │
              │ Cloudflare Tunnel — needed so Microsoft  │
              │ Power Automate (cloud) can reach our RSS │
              │ feed endpoint                            │
              └──────────────┬───────────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────────────────┐
              │ Microsoft Power Automate flow            │
              │  - polls /admin/api/teams/feed.rss       │
              │  - posts Adaptive Cards to a Teams       │
              │    channel via the Flow bot              │
              └──────────────────────────────────────────┘
```

---

## 2. Prerequisites

### VM specifications

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores | 4 cores |
| RAM | 2 GB | 4 GB |
| Disk | 20 GB SSD | 40 GB SSD |
| OS | Ubuntu 22.04 LTS / Debian 12 / RHEL 8+ | Ubuntu 22.04 LTS |
| Network | Outbound HTTPS to internet | Same + inbound HTTPS on port 443 |
| Architecture | x86_64 (amd64) | x86_64 (amd64) |

### Software prerequisites (must be installed on the VM)

- **Docker Engine** version 24+ ([install instructions](https://docs.docker.com/engine/install/))
- **Docker Compose** plugin v2+ (included with modern Docker installations)
- **Git** (for cloning the repository)
- **curl** (for verification commands)

Verify with:
```bash
docker --version          # expect 24.x or newer
docker compose version    # expect v2.x
git --version             # any modern version
```

### Network prerequisites

- **Outbound:** the VM must reach `*.google.com`, `*.reddit.com`, RSS feed hosts, and `news.google.com/_/DotsSplashUi/data/batchexecute` (Google News URL resolution). Allow general HTTPS egress.
- **Inbound:** Microsoft Power Automate must reach the VM on **port 443 (HTTPS)**. This is the most critical network requirement. See [Section 5](#5-make-the-server-publicly-reachable) for ways to expose it.

### Files / credentials you need from the project owner

1. **The project repository** — either a git URL or a `.zip` archive
2. **`.env` file** — production environment variables including secrets (the project owner provides this)
3. **A Teams Power Automate flow URL update access** — the deployment team or the project owner must be able to update the flow's RSS source URL after deployment

---

## 3. Pre-deployment checklist

Before you begin, confirm:

- [ ] VM is provisioned and you can SSH into it
- [ ] VM has at least 2 GB RAM and 20 GB free disk
- [ ] Docker + Docker Compose are installed and working
- [ ] You can `ping 8.8.8.8` and `curl https://www.google.com` successfully
- [ ] You have the project source files (repository URL or zip)
- [ ] You have the production `.env` file
- [ ] You have a plan for exposing port 443 publicly (see Section 5)
- [ ] The team owning the Power Automate flow is available to update one URL after deployment

---

## 4. Step-by-step deployment

### Step 4.1 — SSH into the VM

```bash
ssh <username>@<vm-ip-or-hostname>
```

All commands below run on the VM unless noted otherwise.

### Step 4.2 — Clone (or copy) the project

If using git:
```bash
cd /opt
sudo git clone <repository-url> ainews
sudo chown -R $USER:$USER ainews
cd ainews
```

If using a zip archive instead:
```bash
cd /opt
sudo mkdir ainews
sudo chown $USER:$USER ainews
cd ainews
# scp the zip from your workstation, then:
unzip ainews.zip -d .
```

### Step 4.3 — Place the production `.env` file

Copy the production `.env` file (provided by the project owner) into the project root:

```bash
cp /tmp/.env /opt/ainews/.env       # adjust source path to wherever you uploaded it
chmod 600 .env                       # restrict to owner only
```

**Verify the file contains** (at minimum):

```ini
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/ainews
POSTGRES_DB=ainews
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<strong-password>
REDIS_URL=redis://redis:6379/0
OLLAMA_BASE_URL=http://ollama:11434

# Token protecting the RSS feed exposed to Power Automate
TEAMS_FEED_TOKEN=<long-random-string>

# Optional — only needed for premium scrapers; leave blank if not provided
NEWSAPI_KEY=
TWITTER_BEARER_TOKEN=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
```

> **Important:** if `TEAMS_FEED_TOKEN` is missing, generate one and add it:
> ```bash
> python3 -c "import secrets; print('TEAMS_FEED_TOKEN=' + secrets.token_urlsafe(24))" >> .env
> ```
> Note this value down — it's part of the URL Power Automate will be configured with.

### Step 4.4 — Start the Docker stack

```bash
cd /opt/ainews
docker compose up -d
```

This will:
- Pull/build the necessary container images (takes 5–10 minutes on first run)
- Start 5 containers: `app`, `celery-worker`, `postgres`, `redis`, `ollama`
- Establish internal networking between them

Verify all 5 are running:
```bash
docker compose ps
```

Expected output: 5 containers, all `State = running`, and `app` + `postgres` + `redis` showing `(healthy)`.

### Step 4.5 — Run database migrations

The PostgreSQL container starts empty. Create the schema:

```bash
docker compose exec app alembic upgrade head
```

Expected output: a series of `INFO  [alembic.runtime.migration] Running upgrade ...` lines ending with the latest revision `0004`.

### Step 4.6 — Pull the local LLM model

The Ollama container needs the `llama3.2:3b` model. This is a one-time ~2 GB download:

```bash
docker compose exec ollama ollama pull llama3.2:3b
```

Expect a progress bar. When it finishes, verify:
```bash
docker compose exec ollama ollama list
```
You should see `llama3.2:3b` listed.

### Step 4.7 — Verify the backend is healthy

```bash
curl -s http://localhost:8080/health
```

Expected JSON response:
```json
{
  "status": "healthy",
  "checks": {
    "database": {"status": "ok", "latency_ms": 3.2},
    "redis":    {"status": "ok", "latency_ms": 1.8},
    "ollama":   {"status": "ok", "latency_ms": 5.1, "models": ["llama3.2:3b"]}
  }
}
```

Also verify the dashboard:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/admin/
```
Expected: `200`.

If both work, **the backend is fully deployed locally on the VM**. Next step is exposing it to the internet.

---

## 5. Make the server publicly reachable

Microsoft Power Automate runs in Microsoft's cloud and must reach our RSS feed endpoint over public HTTPS. Pick one of the three options below.

### Option A — Caddy reverse proxy with automatic HTTPS (recommended)

If the VM has a public IP and a DNS name pointing to it, Caddy gives you HTTPS with one config file. Auto-renews certificates.

**Install Caddy:**
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

**Configure** (`/etc/caddy/Caddyfile`):
```
ainews.example.com {
    reverse_proxy localhost:8080
}
```
(Replace `ainews.example.com` with your actual DNS name.)

```bash
sudo systemctl reload caddy
```

Caddy now serves `https://ainews.example.com` with a valid Let's Encrypt cert, forwarding to the app on port 8080. **The public RSS URL becomes** `https://ainews.example.com/admin/api/teams/feed.rss?token=<TEAMS_FEED_TOKEN>`.

### Option B — Cloudflare Tunnel (no public IP needed)

Useful when the VM doesn't have a public IP, or when corporate firewalls block inbound traffic. Cloudflare Tunnel creates a permanent outbound connection from the VM to Cloudflare, and a public URL routes back through it. Free for our scale.

**Install:**
```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
```

**Quick tunnel** (no Cloudflare account needed for testing):
```bash
cloudflared tunnel --url http://localhost:8080
```
Cloudflare prints a public URL like `https://xxxx.trycloudflare.com`. **Note:** this URL is temporary and changes each restart. Fine for testing.

**Stable production tunnel** (requires a free Cloudflare account):
1. Sign up at https://dash.cloudflare.com
2. Run `cloudflared tunnel login` and follow the browser prompts
3. `cloudflared tunnel create ainews`
4. Create a CNAME DNS record in Cloudflare pointing to `<tunnel-id>.cfargotunnel.com`
5. Run `cloudflared tunnel run ainews` (or install as a systemd service)

Stable URL: `https://ainews.<your-cf-zone>.com`.

### Option C — Direct port-forward with self-managed TLS

If your organization uses its own load balancer / reverse proxy infrastructure, point that at `http://<vm-internal-ip>:8080` and configure TLS upstream. Open port 8080 only to the load balancer, not the public internet.

---

## 6. Wire Microsoft Teams integration

This step is performed by the team that owns the existing Power Automate flow. Provide them the new public URL.

### What to send to the Power Automate flow owner

Send them a one-line message:

> "AI-News Aggregator has been deployed to production. Please update the RSS trigger URL in the Teams notification flow to:
>
> `https://<your-production-domain>/admin/api/teams/feed.rss?token=<TEAMS_FEED_TOKEN>`
>
> (Replace the existing ngrok URL with this permanent one and save.)"

### Steps the flow owner will perform

1. https://make.powerautomate.com → **My flows**
2. Open the Teams notification flow
3. Click **Edit**
4. Click the trigger step (`When a feed item is published`)
5. Replace the URL in **The RSS feed URL** field with the new production URL
6. **Save**

The flow then automatically polls the new URL. No code or other config change is needed.

---

## 7. Verify end-to-end

### From the VM

```bash
# 1. All containers running and healthy
docker compose ps

# 2. Health check passes
curl -s http://localhost:8080/health

# 3. RSS feed serves data
curl -s "http://localhost:8080/admin/api/teams/feed.rss?token=$TEAMS_FEED_TOKEN" | head -c 800
```

You should see RSS XML output with article entries.

### From any external machine

Replace the domain with your actual one:

```bash
# 1. Public HTTPS endpoint works
curl -s "https://ainews.example.com/health"

# 2. Public RSS feed works
curl -s "https://ainews.example.com/admin/api/teams/feed.rss?token=<TOKEN>" | head -c 800
```

Both must return data, not connection errors or 404s.

### From Microsoft Power Automate

After the flow URL has been updated:

1. Wait ~15–30 minutes (the RSS trigger's natural poll interval)
2. As soon as a new article gets ingested by the system, a card should arrive in the configured Teams channel
3. Alternatively, the Power Automate flow editor has a **Test → Manually → Run flow** option for an immediate trigger

### Initial article ingestion

The system will start scraping immediately. Within the first hour you should see articles begin appearing on the dashboard and in the Teams channel. Faster sources like Hacker News deliver within 15 minutes.

---

## 8. Keep it running after reboot

Docker Compose configures the containers with the `restart: unless-stopped` policy. As long as the Docker daemon starts automatically on boot, the containers will restart automatically.

Verify Docker is set to start on boot:
```bash
sudo systemctl is-enabled docker
```
Expected: `enabled`. If `disabled`, run:
```bash
sudo systemctl enable docker
```

### Manually restarting the stack

```bash
cd /opt/ainews
docker compose down       # stop everything
docker compose up -d      # start in background
```

### Updating to a new version of the code

```bash
cd /opt/ainews
git pull                                         # get latest code
docker compose build app celery-worker           # rebuild app containers
docker compose up -d                             # apply
docker compose exec app alembic upgrade head     # run any new migrations
```

No data loss — the PostgreSQL volume persists across container restarts.

---

## 9. Routine maintenance

### View logs

```bash
# Real-time logs from the FastAPI app
docker compose logs -f app

# Real-time logs from the scrape worker
docker compose logs -f celery-worker

# All services
docker compose logs -f
```

### Tail just errors / warnings

```bash
docker compose logs app | grep -iE "error|warning|traceback" | tail -50
```

### Check disk usage

The PostgreSQL volume and Ollama model take the most space:
```bash
docker system df -v
```

If disk fills up, prune unused images/volumes:
```bash
docker image prune -a
docker volume prune
```

### Backup the database

```bash
docker compose exec -T postgres pg_dump -U postgres ainews | gzip > /backup/ainews-$(date +%F).sql.gz
```

Recommended: schedule this via cron weekly or daily.

### Restore from a backup

```bash
gunzip -c /backup/ainews-2026-05-14.sql.gz | docker compose exec -T postgres psql -U postgres ainews
```

---

## 10. Troubleshooting

### Containers don't start

```bash
docker compose logs <service-name>   # e.g. docker compose logs app
```

Look for errors. Common causes:
- `.env` file missing or malformed
- Port 8080 already in use by another service on the VM
- Disk full

### `app` container restart-loops with database connection errors

Postgres takes a few seconds to become ready. The `app` container should retry automatically, but if it dies before postgres is ready:

```bash
docker compose down
docker compose up -d postgres redis
sleep 15
docker compose up -d
```

### Health endpoint shows `ollama: error`

Either Ollama hasn't started yet (give it 30 seconds), or the model wasn't pulled. Re-run:
```bash
docker compose exec ollama ollama pull llama3.2:3b
```

### Power Automate polls but no cards appear in Teams

1. Verify the RSS feed URL is reachable from the internet: open it in a browser
2. Check the flow's run history — failed runs show the exact error
3. If the error says `InvalidBotRequestMessageBody`, check that the Adaptive Card template in the flow is the latest version (see `PROJECT_DOCUMENTATION.md`)

### Diagnostic endpoints (admin-internal)

The dashboard exposes several diagnostic endpoints under `/admin/api/diagnostics/`:

- `GET /admin/api/diagnostics/images` — image extraction coverage statistics
- `GET /admin/api/diagnostics/content` — article body extraction statistics
- `GET /admin/api/diagnostics/resolve?url=<google-news-url>` — test Google News URL resolution
- `GET /admin/api/diagnostics/fetch?url=<article-url>` — test full article extraction
- `POST /admin/api/diagnostics/content/refetch/{article_id}` — manually re-extract one article
- `POST /admin/api/diagnostics/teams/test` — send a test card to the configured webhook

Use these to verify pieces of the pipeline are working without browsing the dashboard.

### "No new articles for hours" — what to check

1. `docker compose logs celery-worker | grep "Beat tick"` — should fire every 15 minutes
2. `docker compose logs celery-worker | grep "Scraping source"` — should see scrape attempts
3. Check the admin dashboard's **Sources** page — see if any source has a high `error_count`
4. Check the admin dashboard's **Overview** page for system health

---

## 11. Reference

### Important file paths inside the project

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Defines the 5-container stack |
| `pyproject.toml` | Python dependencies |
| `.env` | Runtime secrets (NOT in git) |
| `alembic/versions/` | Database schema migrations |
| `src/main.py` | FastAPI application entry point |
| `src/config/settings.py` | Configuration loaded from `.env` |
| `src/config/celery_app.py` | Scheduler config |
| `src/dashboard/api/teams_feed.py` | The RSS endpoint Power Automate polls |
| `src/dashboard/templates/` | Admin dashboard HTML templates |
| `src/scrapers/` | Per-source scraper implementations |
| `src/pipeline/` | The 6-stage article processing pipeline |

### Important URLs

| URL | Purpose |
|---|---|
| `https://<host>/admin/` | Admin dashboard |
| `https://<host>/health` | Health check (used by load balancers) |
| `https://<host>/admin/api/teams/feed.rss?token=<TOKEN>` | RSS feed for Power Automate |
| `https://<host>/admin/api/articles` | Articles JSON API |
| `https://<host>/admin/api/sources` | Sources JSON API |
| `https://<host>/admin/api/stats` | Dashboard stats |

### Required environment variables (`.env`)

| Variable | Required? | Example | Purpose |
|---|---|---|---|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://postgres:postgres@postgres:5432/ainews` | DB connection |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Yes | — | Used by the postgres container |
| `REDIS_URL` | Yes | `redis://redis:6379/0` | Celery broker |
| `OLLAMA_BASE_URL` | Yes | `http://ollama:11434` | Local LLM endpoint |
| `TEAMS_FEED_TOKEN` | Yes | `<random 24-char string>` | Shared secret in the RSS URL |
| `TEAMS_NOTIFICATIONS_ENABLED` | No | `true` | Master switch for Teams pushes |
| `TEAMS_NOTIFICATION_MIN_SCORE` | No | `1` | Importance threshold for notifications |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | No | — | Optional cloud LLMs (Ollama is the default) |
| `NEWSAPI_KEY` / `TWITTER_BEARER_TOKEN` | No | — | Optional, only if those scrapers are needed |

### Support contact

If anything in this guide doesn't work as described, contact the project owner:

- **Project owner:** _(fill in your name and email)_
- **Project documentation:** `PROJECT_DOCUMENTATION.md` (same repository root)
- **Source code repository:** _(fill in the git URL)_

---

## Quick deployment checklist (for experienced operators)

```bash
# On the production VM, as a sudo-capable user:
cd /opt
sudo git clone <repo-url> ainews
sudo chown -R $USER:$USER ainews
cd ainews
cp /path/to/production/.env .env
chmod 600 .env
docker compose up -d
docker compose exec app alembic upgrade head
docker compose exec ollama ollama pull llama3.2:3b
curl http://localhost:8080/health

# Then configure Caddy/Cloudflare-Tunnel/your-LB to point HTTPS at port 8080
# Then update the Power Automate flow's RSS URL to the new public hostname
```

That's the whole deployment.

---

**End of document.**
