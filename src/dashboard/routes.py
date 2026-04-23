"""FastAPI router serving the admin dashboard HTML pages."""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

_BASE_DIR = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

router = APIRouter(prefix="/admin")


@router.get("/")
async def overview(request: Request):
    """Dashboard home / overview page."""
    return templates.TemplateResponse("overview.html", {"request": request})


@router.get("/sources")
async def sources_page(request: Request):
    """Sources management page."""
    return templates.TemplateResponse("sources.html", {"request": request})


@router.get("/articles")
async def articles_page(request: Request):
    """Articles browser page."""
    return templates.TemplateResponse("articles.html", {"request": request})


@router.get("/categories")
async def categories_page(request: Request):
    """Categories management page."""
    return templates.TemplateResponse("categories.html", {"request": request})


@router.get("/logs")
async def logs_page(request: Request):
    """Post-logs viewer page."""
    return templates.TemplateResponse("logs.html", {"request": request})


@router.get("/settings")
async def settings_page(request: Request):
    """Settings page."""
    return templates.TemplateResponse("settings.html", {"request": request})
