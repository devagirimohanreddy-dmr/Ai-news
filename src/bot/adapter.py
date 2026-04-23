"""Bot Framework adapter and FastAPI route integration."""

from __future__ import annotations

import logging
import traceback
from typing import Any

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity
from fastapi import APIRouter, Request, Response

from src.config.settings import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Adapter setup                                                                #
# --------------------------------------------------------------------------- #

adapter_settings = BotFrameworkAdapterSettings(
    app_id=settings.AZURE_BOT_APP_ID or "",
    app_password=settings.AZURE_BOT_APP_PASSWORD or "",
)

adapter = BotFrameworkAdapter(adapter_settings)


async def on_error(context: TurnContext, error: Exception) -> None:
    """Global error handler for the Bot Framework adapter.

    Logs the full traceback and sends a user-friendly message so the
    conversation does not hang silently.
    """
    logger.error(
        "Bot adapter error: %s\n%s",
        error,
        traceback.format_exc(),
    )

    # Send a friendly message to the user
    try:
        await context.send_activity(
            "Sorry, something went wrong on my end. Please try again in a moment."
        )
    except Exception:
        logger.error("Failed to send error message to user", exc_info=True)

    # Clear conversation state if you add state management later
    # await conversation_state.delete(context)


adapter.on_turn_error = on_error

# --------------------------------------------------------------------------- #
# FastAPI route                                                                #
# --------------------------------------------------------------------------- #

# The router is module-level so it can be imported and included in the
# FastAPI app:  ``app.include_router(bot_router)``
bot_router = APIRouter()

# The bot instance is injected at startup via ``set_bot()``.
_bot_instance: Any = None


def set_bot(bot: Any) -> None:
    """Register the bot instance that will handle incoming activities.

    Must be called once at application startup before any requests are
    processed.
    """
    global _bot_instance
    _bot_instance = bot


@bot_router.post("/api/messages")
async def messages(request: Request) -> Response:
    """Endpoint that receives Bot Framework activities from Azure Bot Service.

    This is the webhook URL you register in the Azure Bot Channel
    Registration (e.g. ``https://yourhost.com/api/messages``).
    """
    if _bot_instance is None:
        logger.error("Bot instance not registered — call set_bot() at startup")
        return Response(status_code=500, content="Bot not initialized")

    # Read the raw body for authentication verification
    body = await request.body()

    # The Authorization header may be absent during local testing
    auth_header = request.headers.get("Authorization", "")

    try:
        activity = Activity().deserialize(await request.json())

        response = await adapter.process_activity(
            activity,
            auth_header,
            _bot_instance.on_turn,
        )

        if response:
            return Response(
                status_code=response.status,
                content=response.body,
            )
        return Response(status_code=201)

    except PermissionError:
        logger.warning("Authentication failed for incoming activity")
        return Response(status_code=401, content="Unauthorized")
    except Exception:
        logger.exception("Error processing incoming activity")
        return Response(status_code=500, content="Internal server error")


def mount_bot_routes(app: Any, bot: Any) -> None:
    """Convenience function to wire everything together.

    Call this from your FastAPI startup:

    .. code-block:: python

        from src.bot.adapter import mount_bot_routes
        from src.bot.bot_app import NewsBot
        from src.bot.commands.handler import CommandHandler

        handler = CommandHandler()
        bot = NewsBot(command_handler=handler)
        mount_bot_routes(app, bot)

    Args:
        app: The FastAPI application instance.
        bot: A :class:`NewsBot` instance.
    """
    set_bot(bot)
    app.include_router(bot_router)
    logger.info("Bot routes mounted at /api/messages")
