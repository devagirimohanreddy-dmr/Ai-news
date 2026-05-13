"""Dashboard API sub-routers."""

from src.dashboard.api.stats import router as stats_router
from src.dashboard.api.sources import router as sources_router
from src.dashboard.api.articles import router as articles_router
from src.dashboard.api.categories import router as categories_router
from src.dashboard.api.logs import router as logs_router
from src.dashboard.api.settings_api import router as settings_router
from src.dashboard.api.commands import router as commands_router
from src.dashboard.api.diagnostics import router as diagnostics_router
from src.dashboard.api.teams_feed import router as teams_feed_router

__all__ = [
    "stats_router",
    "sources_router",
    "articles_router",
    "categories_router",
    "logs_router",
    "settings_router",
    "commands_router",
    "diagnostics_router",
    "teams_feed_router",
]
