from sqlalchemy import Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ArticleCategory(Base):
    __tablename__ = "article_categories"

    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id"), primary_key=True
    )
