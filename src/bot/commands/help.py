"""Handler for the /help command."""

from __future__ import annotations

from typing import Any

from src.bot.cards.help_card import build_help_card


async def handle_help() -> dict[str, Any]:
    """Return the help card listing all available commands.

    Returns:
        A dict with ``"card"`` (Adaptive Card dict) and ``"text"`` (fallback).
    """
    card = build_help_card()
    return {
        "text": (
            "Available commands: /latest, /search, /subscribe, /unsubscribe, "
            "/digest, /summarize, /settings, /help"
        ),
        "card": card,
    }
