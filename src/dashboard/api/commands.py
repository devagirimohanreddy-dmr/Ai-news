"""Commands API endpoint for the admin dashboard command tester."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from src.models.base import get_session_factory
from src.bot.commands.latest import handle_latest
from src.bot.commands.search import handle_search
from src.bot.commands.subscribe import handle_subscribe, handle_unsubscribe
from src.bot.commands.digest import handle_digest
from src.bot.commands.summarize import handle_summarize
from src.bot.commands.help import handle_help
from src.bot.commands.settings import handle_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/commands", tags=["dashboard-commands"])

# Test user ID for dashboard command testing
DASHBOARD_TEST_USER = "dashboard-tester"


class CommandRequest(BaseModel):
    command: str


def _parse_command(raw: str) -> tuple[str, str]:
    """Parse a raw command string into (command_name, args).

    Examples:
        "/latest"           -> ("/latest", "")
        "/search transformer" -> ("/search", "transformer")
        "/subscribe AI Models" -> ("/subscribe", "AI Models")
    """
    raw = raw.strip()
    if not raw.startswith("/"):
        raw = "/" + raw

    parts = raw.split(None, 1)
    command_name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return command_name, args


@router.post("/execute")
async def execute_command(body: CommandRequest) -> dict[str, Any]:
    """Execute a bot command and return the result as JSON.

    The result dict will have at least a ``"text"`` key, and optionally
    ``"card"`` (single Adaptive Card dict) or ``"cards"`` (list of cards).
    """
    command_name, args = _parse_command(body.command)

    handlers = {
        "/latest": _run_latest,
        "/search": _run_search,
        "/subscribe": _run_subscribe,
        "/unsubscribe": _run_unsubscribe,
        "/digest": _run_digest,
        "/summarize": _run_summarize,
        "/help": _run_help,
        "/settings": _run_settings,
    }

    handler = handlers.get(command_name)
    if handler is None:
        return {
            "text": f"Unknown command: `{command_name}`. Available commands: "
                    f"/latest, /search, /subscribe, /unsubscribe, /digest, "
                    f"/summarize, /settings, /help",
        }

    try:
        result = await handler(args)
        return result
    except Exception:
        logger.exception("Error executing command %s via dashboard", command_name)
        return {
            "text": f"Error executing {command_name}. Check the server logs for details.",
        }


async def _run_latest(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await handle_latest(session, args)


async def _run_search(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await handle_search(session, args)


async def _run_subscribe(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await handle_subscribe(session, args, DASHBOARD_TEST_USER)


async def _run_unsubscribe(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await handle_unsubscribe(session, args, DASHBOARD_TEST_USER)


async def _run_digest(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await handle_digest(session, args)


async def _run_summarize(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        # Pass None for pipeline_factory; summarize will return a message
        # if the article is not already in the DB
        return await handle_summarize(session, args, pipeline_factory=None)


async def _run_help(args: str) -> dict[str, Any]:
    return await handle_help()


async def _run_settings(args: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        return await handle_settings(session, args, DASHBOARD_TEST_USER)
