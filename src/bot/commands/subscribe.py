"""Handlers for the /subscribe and /unsubscribe commands."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.category import Category
from src.models.subscription import Subscription

logger = logging.getLogger(__name__)


async def _fuzzy_find_category(
    session: AsyncSession, name: str
) -> Category | None:
    """Find a category whose name matches (case-insensitive, partial)."""
    # Try exact match first (case-insensitive)
    stmt = select(Category).where(
        func.lower(Category.name) == name.lower()
    )
    result = await session.execute(stmt)
    cat = result.scalar_one_or_none()
    if cat:
        return cat

    # Try partial match
    stmt = select(Category).where(
        func.lower(Category.name).contains(name.lower())
    )
    result = await session.execute(stmt)
    cats = result.scalars().all()

    if len(cats) == 1:
        return cats[0]
    if len(cats) > 1:
        # Return None so the caller can tell the user to be more specific
        return None
    return None


async def _list_matching_categories(
    session: AsyncSession, name: str
) -> list[Category]:
    """Return all categories partially matching *name*."""
    stmt = select(Category).where(
        func.lower(Category.name).contains(name.lower())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def handle_subscribe(
    session: AsyncSession, args: str, user_id: str
) -> dict[str, Any]:
    """Subscribe the user to a category.

    Args:
        session: Async SQLAlchemy session.
        args: Category name (or partial).
        user_id: Teams user ID.

    Returns:
        A dict with ``"text"`` and optionally ``"card"``.
    """
    try:
        category_name = args.strip() if args else ""
        if not category_name:
            # List all available categories
            stmt = select(Category).where(Category.enabled == True).order_by(Category.name)  # noqa: E712
            result = await session.execute(stmt)
            categories = result.scalars().all()
            if not categories:
                return {"text": "No categories are available yet."}

            cat_list = "\n".join(f"- {c.name}" for c in categories)
            return {
                "text": f"Please specify a category. Available categories:\n{cat_list}\n\n"
                        f"Usage: `/subscribe [category]`",
            }

        category = await _fuzzy_find_category(session, category_name)
        if category is None:
            # Check if multiple matches
            matches = await _list_matching_categories(session, category_name)
            if len(matches) > 1:
                names = ", ".join(f"'{c.name}'" for c in matches)
                return {
                    "text": f"Multiple categories match '{category_name}': {names}. "
                            f"Please be more specific.",
                }
            return {
                "text": f"Category '{category_name}' not found. "
                        f"Use `/subscribe` to see available categories.",
            }

        # Check if already subscribed
        stmt = select(Subscription).where(
            Subscription.teams_user_id == user_id,
            Subscription.category_id == category.id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return {"text": f"You are already subscribed to '{category.name}'."}

        # Create subscription
        sub = Subscription(teams_user_id=user_id, category_id=category.id)
        session.add(sub)
        await session.commit()

        return {
            "text": f"Subscribed to '{category.name}'. "
                    f"You will receive alerts when new articles are published in this category.",
        }

    except Exception:
        logger.exception("Error in /subscribe command")
        await session.rollback()
        return {
            "text": "Sorry, something went wrong while subscribing. Please try again.",
        }


async def handle_unsubscribe(
    session: AsyncSession, args: str, user_id: str
) -> dict[str, Any]:
    """Unsubscribe the user from a category.

    Args:
        session: Async SQLAlchemy session.
        args: Category name (or partial).
        user_id: Teams user ID.

    Returns:
        A dict with ``"text"``.
    """
    try:
        category_name = args.strip() if args else ""
        if not category_name:
            # Show current subscriptions
            stmt = (
                select(Subscription, Category)
                .join(Category, Subscription.category_id == Category.id)
                .where(Subscription.teams_user_id == user_id)
            )
            result = await session.execute(stmt)
            rows = result.all()
            if not rows:
                return {"text": "You have no active subscriptions."}

            sub_list = "\n".join(f"- {row.Category.name}" for row in rows)
            return {
                "text": f"Your subscriptions:\n{sub_list}\n\n"
                        f"Usage: `/unsubscribe [category]`",
            }

        category = await _fuzzy_find_category(session, category_name)
        if category is None:
            matches = await _list_matching_categories(session, category_name)
            if len(matches) > 1:
                names = ", ".join(f"'{c.name}'" for c in matches)
                return {
                    "text": f"Multiple categories match '{category_name}': {names}. "
                            f"Please be more specific.",
                }
            return {
                "text": f"Category '{category_name}' not found.",
            }

        # Delete subscription
        stmt = delete(Subscription).where(
            Subscription.teams_user_id == user_id,
            Subscription.category_id == category.id,
        )
        result = await session.execute(stmt)
        await session.commit()

        if result.rowcount == 0:
            return {"text": f"You were not subscribed to '{category.name}'."}

        return {
            "text": f"Unsubscribed from '{category.name}'.",
        }

    except Exception:
        logger.exception("Error in /unsubscribe command")
        await session.rollback()
        return {
            "text": "Sorry, something went wrong while unsubscribing. Please try again.",
        }
