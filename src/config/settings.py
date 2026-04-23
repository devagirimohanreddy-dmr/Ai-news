from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ainews"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM API Keys
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None

    # Local LLM / Scraping
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    FIRECRAWL_BASE_URL: str = "http://localhost:3002"

    # Social Media / News APIs
    TWITTER_BEARER_TOKEN: str | None = None
    NEWSAPI_KEY: str | None = None

    # Azure Bot Service
    AZURE_BOT_APP_ID: str | None = None
    AZURE_BOT_APP_PASSWORD: str | None = None
    TEAMS_CHANNEL_ID: str | None = None

    # Scheduling
    DIGEST_SCHEDULE_HOUR: int = 8
    DIGEST_SCHEDULE_MINUTE: int = 0

    # Thresholds
    BREAKING_NEWS_THRESHOLD: int = 8

    # Logging
    LOG_LEVEL: str = "INFO"

    # Dashboard
    DASHBOARD_PORT: int = 8080


settings = Settings()
