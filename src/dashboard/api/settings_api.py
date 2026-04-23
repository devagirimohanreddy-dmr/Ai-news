"""Settings API endpoints for the admin dashboard.

Settings are persisted to a lightweight ``dashboard_settings`` table so they
survive restarts without requiring a .env rewrite.  On first access the
defaults are pulled from ``src.config.settings``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.config.settings import settings as app_settings
from src.models.base import Base, get_session

router = APIRouter(prefix="/admin/api/settings", tags=["dashboard-settings"])


# --------------------------------------------------------------------------- #
# Lightweight KV table for dashboard-managed settings                          #
# --------------------------------------------------------------------------- #

class DashboardSetting(Base):
    """Simple key/value store for runtime-editable settings."""

    __tablename__ = "dashboard_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# The keys we expose through the dashboard and their default values (sourced
# from the main Settings instance at import time).
_DEFAULTS: dict[str, str] = {
    "digest_schedule_hour": str(app_settings.DIGEST_SCHEDULE_HOUR),
    "digest_schedule_minute": str(app_settings.DIGEST_SCHEDULE_MINUTE),
    "breaking_news_threshold": str(app_settings.BREAKING_NEWS_THRESHOLD),
    "scrape_interval_minutes": "30",
    "log_level": app_settings.LOG_LEVEL,
}


# --------------------------------------------------------------------------- #
# Pydantic schemas                                                             #
# --------------------------------------------------------------------------- #

class SettingsPayload(BaseModel):
    digest_schedule_hour: Optional[int] = None
    digest_schedule_minute: Optional[int] = None
    breaking_news_threshold: Optional[int] = None
    scrape_interval_minutes: Optional[int] = None
    log_level: Optional[str] = None


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("")
async def get_settings(session: AsyncSession = Depends(get_session)):
    """Return current dashboard-managed settings."""

    result = await session.execute(select(DashboardSetting))
    stored = {row.key: row.value for row in result.scalars().all()}

    # Merge defaults with stored overrides
    merged = {**_DEFAULTS, **stored}

    return {
        "digest_schedule_hour": int(merged["digest_schedule_hour"]),
        "digest_schedule_minute": int(merged["digest_schedule_minute"]),
        "breaking_news_threshold": int(merged["breaking_news_threshold"]),
        "scrape_interval_minutes": int(merged["scrape_interval_minutes"]),
        "log_level": merged["log_level"],
    }


@router.put("")
async def update_settings(
    payload: SettingsPayload,
    session: AsyncSession = Depends(get_session),
):
    """Update dashboard-managed settings."""

    updates = payload.model_dump(exclude_unset=True)

    for key, value in updates.items():
        str_value = str(value)
        result = await session.execute(
            select(DashboardSetting).where(DashboardSetting.key == key)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.value = str_value
        else:
            session.add(DashboardSetting(key=key, value=str_value))

    await session.commit()

    # Return the full settings after update
    return await get_settings(session)
