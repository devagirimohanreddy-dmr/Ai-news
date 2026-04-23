"""Service for proactively posting messages to Teams channels and users."""

from __future__ import annotations

import logging
from typing import Any

from botbuilder.core import (
    BotFrameworkAdapter,
    CardFactory,
    MessageFactory,
    TurnContext,
)
from botbuilder.schema import (
    Activity,
    ActivityTypes,
    ChannelAccount,
    ConversationParameters,
    ConversationReference,
)

from src.config.settings import settings
from src.bot.cards.alert_card import build_alert_card
from src.bot.cards.digest_card import build_digest_card

logger = logging.getLogger(__name__)


class PostingService:
    """Posts messages to Teams channels via Bot Framework proactive messaging.

    Proactive messaging requires a stored ``ConversationReference`` for the
    target channel or user.  The adapter handles token acquisition.

    Usage::

        from src.bot.adapter import adapter
        posting = PostingService(adapter)
        await posting.post_alert(article_data)
    """

    def __init__(
        self,
        adapter: BotFrameworkAdapter,
        app_id: str | None = None,
        default_channel_id: str | None = None,
    ):
        self.adapter = adapter
        self.app_id = app_id or settings.AZURE_BOT_APP_ID or ""
        self.default_channel_id = default_channel_id or settings.TEAMS_CHANNEL_ID

        # In-memory store for conversation references.
        # In production, persist these to the database.
        self._conversation_references: dict[str, ConversationReference] = {}

    # ------------------------------------------------------------------ #
    # Conversation reference management                                    #
    # ------------------------------------------------------------------ #

    def save_conversation_reference(self, activity: Activity) -> None:
        """Extract and store a conversation reference from an incoming activity.

        Call this inside your bot's ``on_turn`` or ``on_message_activity``
        to capture references for later proactive messaging.
        """
        ref = TurnContext.get_conversation_reference(activity)
        key = ref.conversation.id if ref.conversation else activity.conversation.id
        self._conversation_references[key] = ref
        logger.debug("Saved conversation reference for %s", key)

    def get_conversation_reference(self, conversation_id: str) -> ConversationReference | None:
        """Retrieve a stored conversation reference by conversation ID."""
        return self._conversation_references.get(conversation_id)

    # ------------------------------------------------------------------ #
    # Posting methods                                                      #
    # ------------------------------------------------------------------ #

    async def post_to_channel(
        self, card: dict[str, Any], channel_id: str | None = None
    ) -> bool:
        """Post an Adaptive Card to a Teams channel.

        Args:
            card: An Adaptive Card dict.
            channel_id: Target channel/conversation ID.
                Falls back to ``TEAMS_CHANNEL_ID`` from settings.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        target_id = channel_id or self.default_channel_id
        if not target_id:
            logger.error("No channel ID provided and TEAMS_CHANNEL_ID is not configured")
            return False

        ref = self._conversation_references.get(target_id)
        if ref is None:
            logger.error(
                "No conversation reference found for channel %s. "
                "The bot must first receive a message from that channel.",
                target_id,
            )
            return False

        try:
            attachment = CardFactory.adaptive_card(card)
            message = MessageFactory.attachment(attachment)

            async def _send(turn_context: TurnContext) -> None:
                await turn_context.send_activity(message)

            await self.adapter.continue_conversation(
                ref, _send, self.app_id
            )
            logger.info("Posted card to channel %s", target_id)
            return True

        except Exception:
            logger.exception("Failed to post to channel %s", target_id)
            return False

    async def post_alert(self, article_data: dict[str, Any]) -> bool:
        """Post a breaking news alert to the main channel.

        Args:
            article_data: Dict suitable for :func:`build_alert_card`.

        Returns:
            True on success, False on failure.
        """
        card = build_alert_card(article_data)
        return await self.post_to_channel(card)

    async def post_digest(self, digest_data: dict[str, Any]) -> bool:
        """Post a daily digest to the main channel.

        Args:
            digest_data: Dict suitable for :func:`build_digest_card`.

        Returns:
            True on success, False on failure.
        """
        card = build_digest_card(digest_data)
        return await self.post_to_channel(card)

    async def send_to_user(
        self, card: dict[str, Any], user_id: str
    ) -> bool:
        """Send a direct message (Adaptive Card) to a specific user.

        This requires a stored conversation reference for the user's
        1-on-1 conversation with the bot.

        Args:
            card: An Adaptive Card dict.
            user_id: The Teams user ID.

        Returns:
            True on success, False on failure.
        """
        # Look for a conversation reference keyed by user_id
        ref = self._conversation_references.get(user_id)
        if ref is None:
            # Try to find it by scanning stored references
            for key, stored_ref in self._conversation_references.items():
                if (
                    stored_ref.user
                    and stored_ref.user.id == user_id
                    and stored_ref.conversation
                    and stored_ref.conversation.conversation_type == "personal"
                ):
                    ref = stored_ref
                    break

        if ref is None:
            logger.warning(
                "No conversation reference found for user %s. "
                "The user must message the bot first.",
                user_id,
            )
            return False

        try:
            attachment = CardFactory.adaptive_card(card)
            message = MessageFactory.attachment(attachment)

            async def _send(turn_context: TurnContext) -> None:
                await turn_context.send_activity(message)

            await self.adapter.continue_conversation(
                ref, _send, self.app_id
            )
            logger.info("Sent card to user %s", user_id)
            return True

        except Exception:
            logger.exception("Failed to send to user %s", user_id)
            return False

    async def send_text_to_channel(
        self, text: str, channel_id: str | None = None
    ) -> bool:
        """Post a plain text message to a Teams channel.

        Args:
            text: The message text.
            channel_id: Target channel/conversation ID.

        Returns:
            True on success, False on failure.
        """
        target_id = channel_id or self.default_channel_id
        if not target_id:
            logger.error("No channel ID provided")
            return False

        ref = self._conversation_references.get(target_id)
        if ref is None:
            logger.error("No conversation reference for channel %s", target_id)
            return False

        try:

            async def _send(turn_context: TurnContext) -> None:
                await turn_context.send_activity(MessageFactory.text(text))

            await self.adapter.continue_conversation(
                ref, _send, self.app_id
            )
            return True

        except Exception:
            logger.exception("Failed to send text to channel %s", target_id)
            return False
