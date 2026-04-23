from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class PostLog(Base):
    __tablename__ = "post_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("articles.id"), nullable=True
    )
    post_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="alert | digest | user_request"
    )
    teams_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending", comment="success | failed | pending"
    )
