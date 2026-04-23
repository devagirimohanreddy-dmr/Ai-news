from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Text, Boolean, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_url_hash", "url_hash"),
        Index("ix_articles_published_at", "published_at"),
        Index("ix_articles_importance_score", "importance_score"),
        # pg_trgm GIN index on title — applied via raw SQL migration:
        #   CREATE INDEX idx_articles_title_trgm ON articles USING gin (title gin_trgm_ops);
        # Full-text search GIN index on markdown_content — applied via raw SQL migration:
        #   CREATE INDEX idx_articles_fulltext ON articles USING gin (to_tsvector('english', markdown_content));
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, comment="NULL for user-submitted articles"
    )
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="SHA-256 of URL")
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    importance_score: Mapped[int] = mapped_column(Integer, default=0)
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False)
    is_user_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    pipeline_status: Mapped[str] = mapped_column(
        String(50),
        default="ingested",
        comment="ingested | deduped | classified | scored | summarized | routed",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    source: Mapped[Optional["Source"]] = relationship("Source", lazy="selectin")
    categories: Mapped[list["Category"]] = relationship(
        "Category", secondary="article_categories", lazy="selectin"
    )
    summaries: Mapped[list["Summary"]] = relationship("Summary", back_populates="article", lazy="selectin")


# Avoid circular import issues — these are for type-checking only
from src.models.source import Source  # noqa: E402, F811
from src.models.category import Category  # noqa: E402, F811
from src.models.summary import Summary  # noqa: E402, F811
