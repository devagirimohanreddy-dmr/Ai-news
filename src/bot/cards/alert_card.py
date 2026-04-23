"""Adaptive Card template for breaking news alerts."""

from __future__ import annotations

from typing import Any


def build_alert_card(article_data: dict[str, Any]) -> dict:
    """Build an Adaptive Card for a breaking news alert.

    Uses a red/attention accent to convey urgency.

    Expected *article_data* keys:
        - title (str)
        - url (str)
        - summary (str)
        - categories (list[str], optional)
        - source_name (str, optional)
        - importance_score (int, optional)
        - published_at (str, optional)
        - author (str, optional)
    """
    title = article_data.get("title", "Breaking News")
    url = article_data.get("url", "")
    summary = article_data.get("summary", "")
    categories = article_data.get("categories") or []
    source_name = article_data.get("source_name") or "Unknown source"
    importance_score = article_data.get("importance_score")
    published_at = article_data.get("published_at") or ""
    author = article_data.get("author") or ""

    category_text = ", ".join(categories) if categories else "General"

    # Build metadata facts
    facts: list[dict] = [
        {"title": "Source", "value": source_name},
    ]
    if author:
        facts.append({"title": "Author", "value": author})
    if published_at:
        facts.append({"title": "Published", "value": published_at})
    if importance_score is not None:
        facts.append({"title": "Score", "value": f"{importance_score}/10"})
    facts.append({"title": "Category", "value": category_text})

    card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "Container",
                "style": "attention",
                "bleed": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": "BREAKING NEWS",
                        "weight": "Bolder",
                        "size": "Small",
                        "color": "Light",
                        "spacing": "None",
                    },
                    {
                        "type": "TextBlock",
                        "text": title,
                        "weight": "Bolder",
                        "size": "Large",
                        "color": "Light",
                        "wrap": True,
                    },
                ],
            },
            {
                "type": "TextBlock",
                "text": summary,
                "wrap": True,
                "spacing": "Medium",
            },
            {
                "type": "FactSet",
                "facts": facts,
                "spacing": "Medium",
            },
        ],
        "actions": [],
    }

    if url:
        card["actions"].append(
            {
                "type": "Action.OpenUrl",
                "title": "Read Full Article",
                "url": url,
            }
        )

    return card
