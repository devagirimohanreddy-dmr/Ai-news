import pathlib

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.config.settings import settings
from src.config.logging import setup_logging

# --------------------------------------------------------------------------- #
# Initialise structured logging before anything else logs                      #
# --------------------------------------------------------------------------- #
setup_logging(log_level=settings.LOG_LEVEL)

app = FastAPI(
    title="AI News Aggregator Bot",
    version="0.1.0",
)


# --------------------------------------------------------------------------- #
# Health endpoint — deep dependency check                                      #
# --------------------------------------------------------------------------- #
from src.config.health import check_health  # noqa: E402


@app.get("/health")
async def health() -> dict:
    """Return aggregated health status for all dependencies.

    Returns ``{"status": "healthy", "checks": {...}}`` when every backend is
    reachable, or ``{"status": "degraded", ...}`` if any check fails.
    """
    return await check_health()


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect the bare hostname to the admin dashboard.

    Without this, the root URL returns FastAPI's default OpenAPI behaviour
    which looks like raw API output to anyone expecting a web UI.
    """
    return RedirectResponse(url="/admin/", status_code=307)


# --------------------------------------------------------------------------- #
# Dashboard routes (admin UI + API)                                            #
# --------------------------------------------------------------------------- #
from src.dashboard import dashboard_router  # noqa: E402
from src.dashboard.api import (  # noqa: E402
    stats_router,
    sources_router,
    articles_router,
    categories_router,
    logs_router,
    settings_router,
    commands_router,
    diagnostics_router,
    teams_feed_router,
)

app.include_router(dashboard_router)
app.include_router(stats_router)
app.include_router(sources_router)
app.include_router(articles_router)
app.include_router(categories_router)
app.include_router(logs_router)
app.include_router(settings_router)
app.include_router(commands_router)
app.include_router(diagnostics_router)
app.include_router(teams_feed_router)

# Serve dashboard static assets (CSS, JS, images)
_static_dir = pathlib.Path(__file__).parent / "dashboard" / "static"
app.mount("/admin/static", StaticFiles(directory=str(_static_dir)), name="admin-static")

# --------------------------------------------------------------------------- #
# Bot routes (Teams webhook endpoint)                                          #
# --------------------------------------------------------------------------- #
from src.bot.adapter import bot_router  # noqa: E402

app.include_router(bot_router)
