"""Sources API endpoints for the admin dashboard."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import get_session
from src.models.source import Source

router = APIRouter(prefix="/admin/api/sources", tags=["dashboard-sources"])


# --------------------------------------------------------------------------- #
# Pydantic schemas                                                             #
# --------------------------------------------------------------------------- #

class SourceCreate(BaseModel):
    name: str
    url: str
    scraper_type: str
    schedule_cron: Optional[str] = None
    priority: int = 1
    config_json: Optional[dict] = None


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    scraper_type: Optional[str] = None
    schedule_cron: Optional[str] = None
    priority: Optional[int] = None
    config_json: Optional[dict] = None


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _source_to_dict(src: Source) -> dict:
    return {
        "id": src.id,
        "name": src.name,
        "url": src.url,
        "scraper_type": src.scraper_type,
        "schedule_cron": src.schedule_cron,
        "priority": src.priority,
        "enabled": src.enabled,
        "last_scraped_at": src.last_scraped_at.isoformat() if src.last_scraped_at else None,
        "error_count": src.error_count,
        "config_json": src.config_json,
        "created_at": src.created_at.isoformat() if src.created_at else None,
    }


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("")
async def list_sources(session: AsyncSession = Depends(get_session)):
    """Return all sources with their current status."""
    result = await session.execute(select(Source).order_by(Source.name))
    sources = result.scalars().all()
    return [_source_to_dict(s) for s in sources]


@router.post("")
async def create_source(payload: SourceCreate, session: AsyncSession = Depends(get_session)):
    """Add a new source."""
    source = Source(
        name=payload.name,
        url=payload.url,
        scraper_type=payload.scraper_type,
        schedule_cron=payload.schedule_cron,
        priority=payload.priority,
        config_json=payload.config_json,
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)
    return _source_to_dict(source)


@router.put("/{source_id}")
async def update_source(
    source_id: int,
    payload: SourceUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an existing source."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)

    await session.commit()
    await session.refresh(source)
    return _source_to_dict(source)


@router.delete("/{source_id}")
async def delete_source(source_id: int, session: AsyncSession = Depends(get_session)):
    """Delete a source."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    await session.delete(source)
    await session.commit()
    return {"ok": True}


@router.post("/{source_id}/toggle")
async def toggle_source(source_id: int, session: AsyncSession = Depends(get_session)):
    """Enable or disable a source."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    source.enabled = not source.enabled
    await session.commit()
    await session.refresh(source)
    return _source_to_dict(source)


@router.post("/{source_id}/scrape")
async def trigger_scrape(source_id: int, session: AsyncSession = Depends(get_session)):
    """Trigger a manual scrape for a source via Celery."""
    result = await session.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        from src.scheduler.scrape_tasks import scrape_source
        task = scrape_source.delay(source_id)
        return {"ok": True, "task_id": task.id, "source": source.name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to dispatch scrape task: {exc}")
