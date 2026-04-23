"""Categories API endpoints for the admin dashboard."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import get_session
from src.models.article_category import ArticleCategory
from src.models.category import Category

router = APIRouter(prefix="/admin/api/categories", tags=["dashboard-categories"])


# --------------------------------------------------------------------------- #
# Pydantic schemas                                                             #
# --------------------------------------------------------------------------- #

class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    keywords: Optional[list[str]] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[list[str]] = None
    enabled: Optional[bool] = None


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _cat_to_dict(cat: Category, article_count: int = 0) -> dict:
    return {
        "id": cat.id,
        "name": cat.name,
        "description": cat.description,
        "keywords": cat.keywords,
        "enabled": cat.enabled,
        "article_count": article_count,
        "created_at": cat.created_at.isoformat() if cat.created_at else None,
    }


# --------------------------------------------------------------------------- #
# Endpoints                                                                    #
# --------------------------------------------------------------------------- #

@router.get("")
async def list_categories(session: AsyncSession = Depends(get_session)):
    """Return all categories with their article counts."""
    query = (
        select(Category, func.count(ArticleCategory.article_id).label("cnt"))
        .outerjoin(ArticleCategory, Category.id == ArticleCategory.category_id)
        .group_by(Category.id)
        .order_by(Category.name)
    )
    result = await session.execute(query)
    rows = result.all()
    return [_cat_to_dict(cat, cnt) for cat, cnt in rows]


@router.post("")
async def create_category(payload: CategoryCreate, session: AsyncSession = Depends(get_session)):
    """Create a new category."""
    cat = Category(
        name=payload.name,
        description=payload.description,
        keywords=payload.keywords,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return _cat_to_dict(cat)


@router.put("/{category_id}")
async def update_category(
    category_id: int,
    payload: CategoryUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an existing category."""
    result = await session.execute(select(Category).where(Category.id == category_id))
    cat = result.scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(cat, field, value)

    await session.commit()
    await session.refresh(cat)
    return _cat_to_dict(cat)


@router.delete("/{category_id}")
async def delete_category(category_id: int, session: AsyncSession = Depends(get_session)):
    """Delete a category."""
    result = await session.execute(select(Category).where(Category.id == category_id))
    cat = result.scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=404, detail="Category not found")

    await session.delete(cat)
    await session.commit()
    return {"ok": True}
