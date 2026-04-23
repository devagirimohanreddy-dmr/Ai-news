from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Text, Boolean, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    scraper_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="rss | api | firecrawl | playwright | readability"
    )
    schedule_cron: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=1, comment="Range 1-3, used for scoring")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    config_json: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="Per-source config: API keys, CSS selectors, etc."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, onupdate=func.now(), nullable=True
    )
