"""
Seed script for the categories table.

Inserts the 11 standard AI news categories used for article classification.
Idempotent — safe to run multiple times; existing categories are skipped.

Usage:
    python -m src.config.seed_categories
"""

import asyncio

from sqlalchemy import select

from src.models.base import get_session_factory
from src.models.category import Category

CATEGORIES = [
    {
        "name": "AI Models, Research & Benchmarks",
        "description": (
            "New AI model releases (GPT, Claude, Gemini, Mistral, Llama, etc.), "
            "research paper breakthroughs, benchmark results, evaluation comparisons, "
            "and academic advances in machine learning."
        ),
        "keywords": [
            "model release", "GPT", "Claude", "Gemini", "Mistral", "Llama",
            "benchmark", "evaluation", "research paper", "arxiv", "transformer",
            "fine-tuning", "training", "SOTA", "state-of-the-art", "parameter",
            "weights",
        ],
    },
    {
        "name": "AI Engineering & Developer Tools",
        "description": (
            "AI development frameworks (LangChain, LlamaIndex, DSPy), agent frameworks, "
            "SDKs, APIs, prompt engineering tools, evaluation tools, observability platforms, "
            "and developer-facing AI tooling."
        ),
        "keywords": [
            "LangChain", "LlamaIndex", "DSPy", "SDK", "API", "framework",
            "developer tool", "prompt engineering", "eval", "observability",
            "vector database", "RAG", "retrieval", "embedding",
        ],
    },
    {
        "name": "Open Source AI Releases",
        "description": (
            "New open-source AI projects, GitHub trending AI repositories, major version "
            "releases of open-source AI tools, breaking changes, and community-driven AI "
            "projects."
        ),
        "keywords": [
            "open source", "GitHub", "release", "v1", "v2", "repository",
            "MIT license", "Apache", "community", "fork", "star", "trending",
        ],
    },
    {
        "name": "AI Products & Features",
        "description": (
            "New features in consumer and enterprise AI products (ChatGPT, Claude, Gemini, "
            "Copilot, etc.), SaaS AI product launches, AI feature integrations in existing "
            "products."
        ),
        "keywords": [
            "ChatGPT", "Copilot", "product launch", "feature", "update",
            "integration", "plugin", "extension", "subscription", "pricing",
            "enterprise", "consumer",
        ],
    },
    {
        "name": "AI Agents & Automation",
        "description": (
            "Autonomous AI agents, agentic workflows, multi-agent systems, automation "
            "pipelines, tools like AutoGPT, CrewAI, and developments in AI autonomy and "
            "tool use."
        ),
        "keywords": [
            "agent", "autonomous", "AutoGPT", "CrewAI", "workflow", "automation",
            "multi-agent", "tool use", "function calling", "orchestration",
            "planning",
        ],
    },
    {
        "name": "AI Use Cases & Applications",
        "description": (
            "Real-world AI deployments, enterprise adoption case studies, industry-specific "
            "AI applications (healthcare, finance, legal, education), and practical AI "
            "implementation stories."
        ),
        "keywords": [
            "use case", "case study", "enterprise", "deployment", "healthcare AI",
            "finance AI", "legal AI", "education AI", "adoption", "implementation",
            "ROI",
        ],
    },
    {
        "name": "AI Industry & Startups",
        "description": (
            "AI company funding rounds, acquisitions, partnerships, startup launches, IPOs, "
            "layoffs, hiring trends, and business developments in the AI industry."
        ),
        "keywords": [
            "funding", "acquisition", "merger", "partnership", "startup",
            "Series A", "Series B", "IPO", "valuation", "layoff", "hiring",
            "raised", "investment",
        ],
    },
    {
        "name": "AI Infrastructure & Big Tech",
        "description": (
            "Cloud AI services (Azure AI, AWS Bedrock, GCP Vertex), AI chip developments "
            "(NVIDIA, AMD, custom silicon), data center infrastructure, and big tech AI "
            "strategy."
        ),
        "keywords": [
            "NVIDIA", "AMD", "GPU", "TPU", "Azure", "AWS", "GCP", "cloud",
            "infrastructure", "data center", "chip", "silicon", "H100", "compute",
        ],
    },
    {
        "name": "AI Policy, Safety & Governance",
        "description": (
            "Government AI regulation, AI safety research, alignment work, AI ethics "
            "debates, legal cases involving AI (copyright, liability), and governance "
            "frameworks."
        ),
        "keywords": [
            "regulation", "policy", "safety", "alignment", "ethics", "governance",
            "copyright", "lawsuit", "EU AI Act", "executive order", "bias",
            "fairness", "responsible AI",
        ],
    },
    {
        "name": "AI Security & Risks",
        "description": (
            "AI-specific security threats (prompt injection, jailbreaks, adversarial "
            "attacks), model vulnerabilities, AI-powered cyber threats, deepfakes, and AI "
            "misuse incidents."
        ),
        "keywords": [
            "security", "prompt injection", "jailbreak", "adversarial",
            "vulnerability", "deepfake", "misuse", "attack", "red team",
            "safety filter", "guardrails",
        ],
    },
    {
        "name": "Learning & Resources",
        "description": (
            "AI tutorials, online courses, educational deep dives, beginner guides, "
            "conference talks, and learning resources for AI practitioners."
        ),
        "keywords": [
            "tutorial", "course", "learning", "guide", "beginner", "advanced",
            "conference", "talk", "workshop", "certification", "bootcamp",
        ],
    },
]


async def seed_categories() -> None:
    """Insert categories into the database, skipping any that already exist."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            for cat_data in CATEGORIES:
                result = await session.execute(
                    select(Category).where(Category.name == cat_data["name"])
                )
                existing = result.scalar_one_or_none()

                if existing is not None:
                    print(f"  [EXISTS]  {cat_data['name']}")
                else:
                    category = Category(
                        name=cat_data["name"],
                        description=cat_data["description"],
                        keywords=cat_data["keywords"],
                    )
                    session.add(category)
                    print(f"  [CREATED] {cat_data['name']}")

    print("\nCategory seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed_categories())
