"""Command router — dispatches bot commands to their handlers."""

from __future__ import annotations

import logging
from typing import Any, Callable

from botbuilder.core import TurnContext
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import get_session_factory
from src.bot.commands.latest import handle_latest
from src.bot.commands.search import handle_search
from src.bot.commands.subscribe import handle_subscribe, handle_unsubscribe
from src.bot.commands.digest import handle_digest
from src.bot.commands.summarize import handle_summarize
from src.bot.commands.settings import handle_settings
from src.bot.commands.help import handle_help

logger = logging.getLogger(__name__)


class CommandHandler:
    """Routes bot commands to their individual handler functions.

    Each command handler receives an async SQLAlchemy session and the
    command arguments, and returns a dict with at least a ``"text"`` key
    (and optionally ``"card"`` / ``"cards"``).
    """

    def __init__(self, pipeline_factory: Callable[[AsyncSession], Any] | None = None):
        """
        Args:
            pipeline_factory: Optional callable that creates an ArticlePipeline
                bound to a given session.  Used by ``/summarize``.
        """
        self.pipeline_factory = pipeline_factory

    async def handle(
        self, command: str, args: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        """Route *command* to the appropriate handler.

        Args:
            command: The slash command (e.g. ``"/latest"``).
            args: Everything after the command name.
            turn_context: Bot Framework turn context (used for user ID, etc.).

        Returns:
            A dict with ``"text"`` (str), and optionally ``"card"`` (dict)
            or ``"cards"`` (list[dict]).
        """
        user_id = turn_context.activity.from_property.id if turn_context.activity.from_property else "unknown"

        handlers = {
            "/latest": self._handle_latest,
            "/search": self._handle_search,
            "/subscribe": self._handle_subscribe,
            "/unsubscribe": self._handle_unsubscribe,
            "/digest": self._handle_digest,
            "/summarize": self._handle_summarize,
            "/settings": self._handle_settings,
            "/help": self._handle_help,
        }

        handler = handlers.get(command.lower())
        if handler is None:
            return {
                "text": f"Unknown command: `{command}`. Type `/help` to see available commands.",
            }

        try:
            return await handler(args, user_id, turn_context)
        except Exception:
            logger.exception("Unhandled error in command %s", command)
            return {
                "text": "An unexpected error occurred. Please try again later.",
            }

    # ------------------------------------------------------------------ #
    # Private wrappers — each opens its own session                       #
    # ------------------------------------------------------------------ #

    async def _handle_latest(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_latest(session, args)

    async def _handle_search(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_search(session, args)

    async def _handle_subscribe(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_subscribe(session, args, user_id)

    async def _handle_unsubscribe(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_unsubscribe(session, args, user_id)

    async def _handle_digest(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_digest(session, args)

    async def _handle_summarize(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_summarize(session, args, self.pipeline_factory)

    async def _handle_settings(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            return await handle_settings(session, args, user_id)

    async def _handle_help(
        self, args: str, user_id: str, turn_context: TurnContext
    ) -> dict[str, Any]:
        return await handle_help()
