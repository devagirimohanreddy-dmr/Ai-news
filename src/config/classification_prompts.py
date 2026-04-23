"""Category definitions and prompts used by the ClassifyStage."""

# The 11 standard AI news categories — names must match the seed data in
# src/config/seed_categories.py exactly.
CATEGORY_NAMES: list[str] = [
    "AI Models, Research & Benchmarks",
    "AI Engineering & Developer Tools",
    "Open Source AI Releases",
    "AI Products & Features",
    "AI Agents & Automation",
    "AI Use Cases & Applications",
    "AI Industry & Startups",
    "AI Infrastructure & Big Tech",
    "AI Policy, Safety & Governance",
    "AI Security & Risks",
    "Learning & Resources",
]

CLASSIFICATION_SYSTEM_PROMPT: str = (
    "You are a news classifier. Given an article, assign it to 1-3 categories "
    "from this list: ["
    + ", ".join(f'"{name}"' for name in CATEGORY_NAMES)
    + "]. Respond in JSON: {\"categories\": [\"Category Name 1\", \"Category Name 2\"]}"
)

CLASSIFICATION_USER_PROMPT_TEMPLATE: str = (
    "Title: {title}\n\n"
    "Content:\n{content}"
)

# Keyword-to-category fallback mapping.  Each key is a lowercased keyword;
# values are category names that the keyword maps to.  Used when the LLM is
# unavailable.
KEYWORD_CATEGORY_MAP: dict[str, list[str]] = {
    # AI Models, Research & Benchmarks
    "gpt": ["AI Models, Research & Benchmarks"],
    "claude": ["AI Models, Research & Benchmarks"],
    "gemini": ["AI Models, Research & Benchmarks"],
    "llama": ["AI Models, Research & Benchmarks"],
    "mistral": ["AI Models, Research & Benchmarks"],
    "benchmark": ["AI Models, Research & Benchmarks"],
    "arxiv": ["AI Models, Research & Benchmarks"],
    "transformer": ["AI Models, Research & Benchmarks"],
    "fine-tuning": ["AI Models, Research & Benchmarks"],
    "state-of-the-art": ["AI Models, Research & Benchmarks"],
    "sota": ["AI Models, Research & Benchmarks"],
    # AI Engineering & Developer Tools
    "langchain": ["AI Engineering & Developer Tools"],
    "llamaindex": ["AI Engineering & Developer Tools"],
    "sdk": ["AI Engineering & Developer Tools"],
    "api": ["AI Engineering & Developer Tools"],
    "framework": ["AI Engineering & Developer Tools"],
    "rag": ["AI Engineering & Developer Tools"],
    "vector database": ["AI Engineering & Developer Tools"],
    "embedding": ["AI Engineering & Developer Tools"],
    # Open Source AI Releases
    "open source": ["Open Source AI Releases"],
    "open-source": ["Open Source AI Releases"],
    "github": ["Open Source AI Releases"],
    "repository": ["Open Source AI Releases"],
    # AI Products & Features
    "chatgpt": ["AI Products & Features"],
    "copilot": ["AI Products & Features"],
    "product launch": ["AI Products & Features"],
    "plugin": ["AI Products & Features"],
    # AI Agents & Automation
    "agent": ["AI Agents & Automation"],
    "autonomous": ["AI Agents & Automation"],
    "autogpt": ["AI Agents & Automation"],
    "crewai": ["AI Agents & Automation"],
    "multi-agent": ["AI Agents & Automation"],
    "workflow": ["AI Agents & Automation"],
    # AI Use Cases & Applications
    "use case": ["AI Use Cases & Applications"],
    "case study": ["AI Use Cases & Applications"],
    "deployment": ["AI Use Cases & Applications"],
    "healthcare ai": ["AI Use Cases & Applications"],
    # AI Industry & Startups
    "funding": ["AI Industry & Startups"],
    "acquisition": ["AI Industry & Startups"],
    "startup": ["AI Industry & Startups"],
    "ipo": ["AI Industry & Startups"],
    "valuation": ["AI Industry & Startups"],
    # AI Infrastructure & Big Tech
    "nvidia": ["AI Infrastructure & Big Tech"],
    "gpu": ["AI Infrastructure & Big Tech"],
    "tpu": ["AI Infrastructure & Big Tech"],
    "data center": ["AI Infrastructure & Big Tech"],
    "h100": ["AI Infrastructure & Big Tech"],
    # AI Policy, Safety & Governance
    "regulation": ["AI Policy, Safety & Governance"],
    "alignment": ["AI Policy, Safety & Governance"],
    "eu ai act": ["AI Policy, Safety & Governance"],
    "governance": ["AI Policy, Safety & Governance"],
    "ethics": ["AI Policy, Safety & Governance"],
    # AI Security & Risks
    "prompt injection": ["AI Security & Risks"],
    "jailbreak": ["AI Security & Risks"],
    "adversarial": ["AI Security & Risks"],
    "deepfake": ["AI Security & Risks"],
    "vulnerability": ["AI Security & Risks"],
    # Learning & Resources
    "tutorial": ["Learning & Resources"],
    "course": ["Learning & Resources"],
    "workshop": ["Learning & Resources"],
    "bootcamp": ["Learning & Resources"],
    "certification": ["Learning & Resources"],
}
