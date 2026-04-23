"""initial schema: sources, articles, categories, summaries, subscriptions, post_logs

Revision ID: 0001
Revises:
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- sources ---
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column(
            "scraper_type",
            sa.String(length=50),
            nullable=False,
            comment="rss | api | firecrawl | playwright | readability",
        ),
        sa.Column("schedule_cron", sa.String(length=100), nullable=True),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=True,
            server_default="1",
            comment="Range 1-3, used for scoring",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("last_scraped_at", sa.DateTime(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column(
            "config_json",
            sa.JSON(),
            nullable=True,
            comment="Per-source config: API keys, CSS selectors, etc.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- categories ---
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "keywords",
            sa.JSON(),
            nullable=True,
            comment="List of keyword strings for matching",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- articles ---
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id"),
            nullable=True,
            comment="NULL for user-submitted articles",
        ),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column(
            "url_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 of URL",
        ),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("markdown_content", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=512), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("importance_score", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("is_breaking", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column(
            "is_user_submitted", sa.Boolean(), nullable=True, server_default="false"
        ),
        sa.Column(
            "pipeline_status",
            sa.String(length=50),
            nullable=True,
            server_default="ingested",
            comment="ingested | deduped | classified | scored | summarized | routed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("ix_articles_url_hash", "articles", ["url_hash"])
    op.create_index("ix_articles_published_at", "articles", ["published_at"])
    op.create_index("ix_articles_importance_score", "articles", ["importance_score"])

    # --- article_categories (many-to-many join) ---
    op.create_table(
        "article_categories",
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("article_id", "category_id"),
    )

    # --- summaries ---
    op.create_table(
        "summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id"),
            nullable=False,
        ),
        sa.Column("headline", sa.String(length=512), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("llm_provider", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- subscriptions ---
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("teams_user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "teams_user_id", "category_id", name="uq_subscription_user_category"
        ),
    )

    # --- post_logs ---
    op.create_table(
        "post_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id"),
            nullable=True,
        ),
        sa.Column(
            "post_type",
            sa.String(length=50),
            nullable=False,
            comment="alert | digest | user_request",
        ),
        sa.Column("teams_channel", sa.String(length=255), nullable=True),
        sa.Column(
            "posted_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=True,
            server_default="pending",
            comment="success | failed | pending",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("post_logs")
    op.drop_table("subscriptions")
    op.drop_table("summaries")
    op.drop_table("article_categories")
    op.drop_index("ix_articles_importance_score", table_name="articles")
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_index("ix_articles_url_hash", table_name="articles")
    op.drop_table("articles")
    op.drop_table("categories")
    op.drop_table("sources")
