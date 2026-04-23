"""Main bot handler — Microsoft Teams ActivityHandler."""

from __future__ import annotations

import logging
from typing import Any

from botbuilder.core import (
    ActivityHandler,
    CardFactory,
    MessageFactory,
    TurnContext,
)
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount

from src.bot.commands.handler import CommandHandler

logger = logging.getLogger(__name__)


class NewsBot(ActivityHandler):
    """Teams bot that handles messages and routes slash commands.

    Delegates all ``/command`` messages to :class:`CommandHandler` and
    sends responses as Adaptive Cards (with a text fallback for clients
    that do not support cards).
    """

    def __init__(self, command_handler: CommandHandler):
        super().__init__()
        self.command_handler = command_handler

    # ------------------------------------------------------------------ #
    # Message handling                                                     #
    # ------------------------------------------------------------------ #

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        """Process an incoming message from a Teams user."""
        text = (turn_context.activity.text or "").strip()

        # Strip bot @mention prefix that Teams adds in channel messages
        text = self._strip_mention(text, turn_context)

        if not text:
            await turn_context.send_activity(
                MessageFactory.text("I didn't catch that. Type `/help` to see what I can do.")
            )
            return

        # Check for a slash command
        if text.startswith("/"):
            command, _, args = text.partition(" ")
            result = await self.command_handler.handle(command, args, turn_context)
            await self._send_result(turn_context, result)
            return

        # Check if the message looks like a URL (quick summarize shortcut)
        if text.startswith("http://") or text.startswith("https://"):
            result = await self.command_handler.handle("/summarize", text, turn_context)
            await self._send_result(turn_context, result)
            return

        # Default: unknown input
        await turn_context.send_activity(
            MessageFactory.text(
                f"I'm not sure what you mean by \"{text[:100]}\". "
                f"Type `/help` to see available commands."
            )
        )

    # ------------------------------------------------------------------ #
    # Membership events                                                    #
    # ------------------------------------------------------------------ #

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ) -> None:
        """Send a welcome message when the bot or a user is added."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome = (
                    "Welcome to the AI News Aggregator Bot! "
                    "Type `/help` to see all available commands."
                )
                await turn_context.send_activity(MessageFactory.text(welcome))

    # ------------------------------------------------------------------ #
    # Adaptive Card action handling                                        #
    # ------------------------------------------------------------------ #

    async def on_adaptive_card_invoke(self, turn_context: TurnContext) -> Any:
        """Handle Action.Submit payloads from Adaptive Cards."""
        data = turn_context.activity.value or {}
        action = data.get("action")

        if action == "summarize":
            url = data.get("url", "")
            result = await self.command_handler.handle("/summarize", url, turn_context)
            await self._send_result(turn_context, result)
            return self._invoke_response(200)

        return self._invoke_response(200)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip_mention(text: str, turn_context: TurnContext) -> str:
        """Remove the bot's @mention from the beginning of the text."""
        if turn_context.activity.entities:
            for entity in turn_context.activity.entities:
                if entity.type == "mention":
                    mention_text = entity.additional_properties.get("text", "")
                    if mention_text and text.startswith(mention_text):
                        text = text[len(mention_text):].strip()
                        break
        return text

    @staticmethod
    async def _send_result(turn_context: TurnContext, result: dict[str, Any]) -> None:
        """Send a command result back to the user.

        Supports three result shapes:
        - ``{"card": dict}`` — single Adaptive Card
        - ``{"cards": list[dict]}`` — multiple Adaptive Cards
        - ``{"text": str}`` — plain text fallback
        """
        # Show typing indicator if requested
        if result.get("show_typing"):
            typing_activity = Activity(type=ActivityTypes.typing)
            await turn_context.send_activity(typing_activity)

        # Send single card
        card = result.get("card")
        if card:
            attachment = CardFactory.adaptive_card(card)
            message = MessageFactory.attachment(attachment)
            # Set fallback text
            message.text = result.get("text", "")
            await turn_context.send_activity(message)
            return

        # Send multiple cards
        cards = result.get("cards")
        if cards:
            # Send header text first
            header = result.get("text", "")
            if header:
                await turn_context.send_activity(MessageFactory.text(header))

            for c in cards:
                attachment = CardFactory.adaptive_card(c)
                msg = MessageFactory.attachment(attachment)
                await turn_context.send_activity(msg)
            return

        # Text only
        text = result.get("text", "No response.")
        await turn_context.send_activity(MessageFactory.text(text))

    @staticmethod
    def _invoke_response(status_code: int) -> dict:
        """Build an invoke response for Adaptive Card actions."""
        return {"status": status_code, "body": None}
