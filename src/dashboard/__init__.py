"""Admin dashboard package — FastAPI router + HTMX templates."""

from src.dashboard.routes import router as dashboard_router

__all__ = ["dashboard_router"]
