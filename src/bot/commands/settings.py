"""Handler for the /settings command."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, String, Integer, Text, DateTime, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base
from src.models.subscription import Subscription
from src.models.category import Category

logger = logging.getLogger(__name__)


class UserPreference(Base):
    """Stores per-user preferences as a JSON blob.

    This is a lightweight table; extend the ``preferences`` JSON column
    as needed without schema migrations.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    teams_user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    preferences: Mapped[dict] = mapped_column(
        Text, nullable=False, default="{}"
    )
    created_at: Mapped[Any] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Any] = mapped_column(
        DateTime, onupdate=func.now(), nullable=True
    )


# Default preferences for new users
DEFAULT_PREFERENCES: dict[str, Any] = {
    "digest_enabled": True,
    "digest_hour": 8,
    "alert_enabled": True,
    "min_importance_score": 5,
}


async def handle_settings(
    session: AsyncSession, args: str, user_id: str
) -> dict[str, Any]:
    """Show or update user preferences.

    Without arguments, shows current settings.  With ``key=value`` pairs,
    updates the specified preferences.

    Args:
        session: Async SQLAlchemy session.
        args: Optional ``key=value`` pairs (space-separated).
        user_id: Teams user ID.

    Returns:
        A dict with ``"text"`` and optionally ``"card"``.
    """
    try:
        # Load or create user preferences
        stmt = select(UserPreference).where(
            UserPreference.teams_user_id == user_id
        )
        result = await session.execute(stmt)
        pref_row = result.scalar_one_or_none()

        if pref_row is None:
            pref_row = UserPreference(
                teams_user_id=user_id,
                preferences=json.dumps(DEFAULT_PREFERENCES),
            )
            session.add(pref_row)
            await session.flush()

        prefs = _parse_preferences(pref_row.preferences)

        # If no args, show current settings
        if not args or not args.strip():
            # Also load subscriptions
            sub_stmt = (
                select(Category.name)
                .join(Subscription, Subscription.category_id == Category.id)
                .where(Subscription.teams_user_id == user_id)
            )
            sub_result = await session.execute(sub_stmt)
            subscribed_cats = [row[0] for row in sub_result.all()]

            return {
                "text": _format_settings(prefs, subscribed_cats),
                "card": _build_settings_card(prefs, subscribed_cats),
            }

        # Parse and apply updates
        updates = _parse_setting_args(args.strip())
        if not updates:
            return {
                "text": "Could not parse settings. Use format: `/settings key=value`\n"
                        "Available keys: digest_enabled, digest_hour, alert_enabled, min_importance_score",
            }

        for key, value in updates.items():
            if key in DEFAULT_PREFERENCES:
                prefs[key] = value

        pref_row.preferences = json.dumps(prefs)
        await session.commit()

        return {
            "text": f"Settings updated: {', '.join(f'{k}={v}' for k, v in updates.items())}",
        }

    except Exception:
        logger.exception("Error in /settings command")
        await session.rollback()
        return {
            "text": "Sorry, something went wrong while managing settings. Please try again.",
        }


def _parse_preferences(raw: str | dict) -> dict[str, Any]:
    """Parse preferences from the DB column (JSON string or dict)."""
    if isinstance(raw, dict):
        return {**DEFAULT_PREFERENCES, **raw}
    try:
        parsed = json.loads(raw)
        return {**DEFAULT_PREFERENCES, **parsed}
    except (json.JSONDecodeError, TypeError):
        return dict(DEFAULT_PREFERENCES)


def _parse_setting_args(args: str) -> dict[str, Any]:
    """Parse ``key=value`` pairs from a string."""
    updates: dict[str, Any] = {}
    for part in args.split():
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()

        # Type coercion
        if value.lower() in ("true", "yes", "1"):
            updates[key] = True
        elif value.lower() in ("false", "no", "0"):
            updates[key] = False
        else:
            try:
                updates[key] = int(value)
            except ValueError:
                updates[key] = value

    return updates


def _format_settings(prefs: dict[str, Any], subscriptions: list[str]) -> str:
    """Format settings as a readable text block."""
    lines = [
        "**Your Settings:**",
        f"- Daily digest: {'Enabled' if prefs.get('digest_enabled') else 'Disabled'}",
        f"- Digest delivery hour (UTC): {prefs.get('digest_hour', 8)}",
        f"- Breaking alerts: {'Enabled' if prefs.get('alert_enabled') else 'Disabled'}",
        f"- Minimum importance score for alerts: {prefs.get('min_importance_score', 5)}",
        "",
        "**Subscriptions:**",
    ]
    if subscriptions:
        for cat in subscriptions:
            lines.append(f"- {cat}")
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("To update, use: `/settings key=value` (e.g. `/settings digest_hour=9`)")
    return "\n".join(lines)


def _build_settings_card(prefs: dict[str, Any], subscriptions: list[str]) -> dict:
    """Build an Adaptive Card displaying user settings."""
    facts = [
        {"title": "Daily Digest", "value": "Enabled" if prefs.get("digest_enabled") else "Disabled"},
        {"title": "Digest Hour (UTC)", "value": str(prefs.get("digest_hour", 8))},
        {"title": "Breaking Alerts", "value": "Enabled" if prefs.get("alert_enabled") else "Disabled"},
        {"title": "Min Score for Alerts", "value": str(prefs.get("min_importance_score", 5))},
        {
            "title": "Subscriptions",
            "value": ", ".join(subscriptions) if subscriptions else "(none)",
        },
    ]

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Your Settings",
                "weight": "Bolder",
                "size": "Medium",
            },
            {
                "type": "FactSet",
                "facts": facts,
            },
            {
                "type": "TextBlock",
                "text": "To update: `/settings key=value`",
                "size": "Small",
                "isSubtle": True,
                "spacing": "Medium",
            },
        ],
    }
