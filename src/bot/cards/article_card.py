"""Adaptive Card template for a single article."""

from __future__ import annotations

from typing import Any


def build_article_card(article_data: dict[str, Any]) -> dict:
    """Build an Adaptive Card for a single article.

    Expected *article_data* keys:
        - title (str)
        - url (str)
        - summary (str, optional)
        - headline (str, optional) — short summary headline
        - categories (list[str], optional)
        - source_name (str, optional)
        - importance_score (int, optional)
        - published_at (str, optional) — ISO datetime string
        - author (str, optional)
    """
    title = article_data.get("title", "Untitled")
    url = article_data.get("url", "")
    summary = article_data.get("summary") or article_data.get("headline") or ""
    categories = article_data.get("categories") or []
    source_name = article_data.get("source_name") or "Unknown source"
    importance_score = article_data.get("importance_score")
    published_at = article_data.get("published_at") or ""
    author = article_data.get("author") or ""

    # Build category tags
    category_text = ", ".join(categories) if categories else "Uncategorized"

    # Meta line
    meta_parts: list[str] = []
    if source_name:
        meta_parts.append(f"**Source:** {source_name}")
    if author:
        meta_parts.append(f"**Author:** {author}")
    if published_at:
        meta_parts.append(f"**Published:** {published_at}")
    if importance_score is not None:
        meta_parts.append(f"**Score:** {importance_score}/10")
    meta_line = "  |  ".join(meta_parts)

    body: list[dict] = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": category_text,
            "color": "Accent",
            "size": "Small",
            "spacing": "None",
        },
    ]

    if meta_line:
        body.append(
            {
                "type": "TextBlock",
                "text": meta_line,
                "size": "Small",
                "isSubtle": True,
                "wrap": True,
                "spacing": "Small",
            }
        )

    if summary:
        body.append(
            {
                "type": "TextBlock",
                "text": summary,
                "wrap": True,
                "spacing": "Medium",
            }
        )

    card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
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
        card["actions"].append(
            {
                "type": "Action.Submit",
                "title": "Summarize",
                "data": {"action": "summarize", "url": url},
            }
        )

    return card
