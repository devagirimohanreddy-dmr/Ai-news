"""Adaptive Card template for the /help command."""

from __future__ import annotations


COMMANDS: list[dict[str, str]] = [
    {
        "command": "/latest",
        "description": "Show the 5 most recent articles. Use `/latest [category]` to filter by category.",
    },
    {
        "command": "/search [query]",
        "description": "Full-text search across all articles. Returns top 10 results ranked by relevance.",
    },
    {
        "command": "/subscribe [category]",
        "description": "Subscribe to a category to receive alerts when new articles are published.",
    },
    {
        "command": "/unsubscribe [category]",
        "description": "Unsubscribe from a category.",
    },
    {
        "command": "/digest",
        "description": "Generate an on-demand digest of articles from the last 24 hours.",
    },
    {
        "command": "/summarize [URL]",
        "description": "Summarize an article by URL. If already in the database, returns the cached summary.",
    },
    {
        "command": "/settings",
        "description": "View and manage your notification preferences.",
    },
    {
        "command": "/help",
        "description": "Show this help card with all available commands.",
    },
]


def build_help_card() -> dict:
    """Build an Adaptive Card listing all available bot commands."""

    rows: list[dict] = []
    for cmd in COMMANDS:
        rows.append(
            {
                "type": "ColumnSet",
                "spacing": "Small",
                "columns": [
                    {
                        "type": "Column",
                        "width": "auto",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": f"`{cmd['command']}`",
                                "weight": "Bolder",
                                "size": "Small",
                                "wrap": False,
                            }
                        ],
                    },
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": cmd["description"],
                                "size": "Small",
                                "wrap": True,
                                "isSubtle": True,
                            }
                        ],
                    },
                ],
            }
        )

    card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "AI News Aggregator Bot",
                "weight": "Bolder",
                "size": "Large",
            },
            {
                "type": "TextBlock",
                "text": "Here are the commands you can use:",
                "size": "Small",
                "isSubtle": True,
                "spacing": "None",
            },
            *rows,
            {
                "type": "TextBlock",
                "text": "Tip: You can also paste a URL and I will attempt to summarize it.",
                "size": "Small",
                "isSubtle": True,
                "spacing": "Large",
                "separator": True,
                "wrap": True,
            },
        ],
    }

    return card
