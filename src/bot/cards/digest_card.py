"""Adaptive Card template for the daily digest."""

from __future__ import annotations

from typing import Any


def build_digest_card(digest_data: dict[str, Any]) -> dict:
    """Build an Adaptive Card for a daily/on-demand digest.

    Expected *digest_data* keys:
        - date (str) — e.g. "2026-04-23"
        - total_count (int) — number of articles
        - top_stories (list[dict]) — up to 3 highest-scored articles,
              each with ``title``, ``url``, ``summary``, ``importance_score``
        - categories (dict[str, list[dict]]) — category name -> list of
              articles (each with ``title``, ``url``)
    """
    date = digest_data.get("date", "Today")
    total_count = digest_data.get("total_count", 0)
    top_stories: list[dict] = digest_data.get("top_stories") or []
    categories: dict[str, list[dict]] = digest_data.get("categories") or {}

    body: list[dict] = [
        {
            "type": "TextBlock",
            "text": f"Daily Digest  --  {date}",
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"{total_count} article(s) in the last 24 hours",
            "size": "Small",
            "isSubtle": True,
            "spacing": "None",
        },
    ]

    # Top stories section
    if top_stories:
        body.append(
            {
                "type": "TextBlock",
                "text": "Top Stories",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Large",
                "separator": True,
            }
        )

        for story in top_stories[:5]:
            story_title = story.get("title", "Untitled")
            story_url = story.get("url", "")
            story_summary = story.get("summary", "")
            story_score = story.get("importance_score")

            score_text = f"  (Score: {story_score}/10)" if story_score is not None else ""

            items: list[dict] = [
                {
                    "type": "TextBlock",
                    "text": f"[{story_title}]({story_url}){score_text}" if story_url else story_title,
                    "weight": "Bolder",
                    "wrap": True,
                    "size": "Small",
                },
            ]
            if story_summary:
                items.append(
                    {
                        "type": "TextBlock",
                        "text": story_summary[:200],
                        "wrap": True,
                        "size": "Small",
                        "isSubtle": True,
                        "spacing": "None",
                    }
                )

            body.append(
                {
                    "type": "Container",
                    "spacing": "Small",
                    "items": items,
                }
            )

    # Category sections (collapsible via Action.ToggleVisibility)
    if categories:
        body.append(
            {
                "type": "TextBlock",
                "text": "By Category",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Large",
                "separator": True,
            }
        )

        for cat_name, articles in categories.items():
            container_id = f"cat_{cat_name.replace(' ', '_').lower()}"

            body.append(
                {
                    "type": "Container",
                    "spacing": "Small",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": f"{cat_name} ({len(articles)})",
                            "weight": "Bolder",
                            "size": "Small",
                            "color": "Accent",
                        },
                    ],
                    "selectAction": {
                        "type": "Action.ToggleVisibility",
                        "targetElements": [container_id],
                    },
                }
            )

            article_items: list[dict] = []
            for art in articles[:10]:
                art_title = art.get("title", "Untitled")
                art_url = art.get("url", "")
                text = f"- [{art_title}]({art_url})" if art_url else f"- {art_title}"
                article_items.append(
                    {
                        "type": "TextBlock",
                        "text": text,
                        "wrap": True,
                        "size": "Small",
                    }
                )

            body.append(
                {
                    "type": "Container",
                    "id": container_id,
                    "isVisible": False,
                    "spacing": "None",
                    "items": article_items,
                }
            )

    card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }

    return card
