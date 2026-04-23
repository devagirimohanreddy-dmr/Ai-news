"""Scoring rules and keyword lists for the ScoreStage."""

# Keywords that signal high-importance / breaking news.
# Matching is case-insensitive against title + markdown_content.
BREAKING_KEYWORDS: list[str] = [
    "GPT-5",
    "GPT-6",
    "acquired",
    "acquisition",
    "security vulnerability",
    "shutdown",
    "banned",
    "breakthrough",
    "world first",
]

# Maximum points from keyword-based scoring.
MAX_KEYWORD_SCORE: int = 3

# Maximum points from source priority.
MAX_SOURCE_PRIORITY_SCORE: int = 3

# Maximum points from LLM-based scoring.
MAX_LLM_SCORE: int = 4

# Overall cap for importance_score.
MAX_TOTAL_SCORE: int = 10

# LLM scoring prompt
SCORING_SYSTEM_PROMPT: str = (
    "You are an AI news importance scorer. "
    "Rate this article's significance for AI professionals from 1-10. "
    "Respond in JSON: {\"score\": N, \"reason\": \"...\"}"
)

SCORING_USER_PROMPT_TEMPLATE: str = (
    "Title: {title}\n\n"
    "Content:\n{content}"
)
