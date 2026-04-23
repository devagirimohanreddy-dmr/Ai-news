"""add pg_trgm GIN index on articles.title and full-text search GIN index on articles.markdown_content

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pg_trgm GIN index for fuzzy / LIKE searches on article titles.
    # Requires the pg_trgm extension (already enabled on the database).
    op.execute(
        "CREATE INDEX idx_articles_title_trgm ON articles USING gin (title gin_trgm_ops);"
    )

    # Full-text search GIN index on markdown_content using English dictionary.
    op.execute(
        "CREATE INDEX idx_articles_fulltext ON articles "
        "USING gin (to_tsvector('english', markdown_content));"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_articles_fulltext;")
    op.execute("DROP INDEX IF EXISTS idx_articles_title_trgm;")
