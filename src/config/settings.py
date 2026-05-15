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

    # Firecrawl is optional. Leave empty to disable — health check will
    # report "skipped" and any firecrawl-typed sources will be skipped at
    # scrape time. Set to the hosted API ("https://api.firecrawl.dev") with
    # a FIRECRAWL_API_KEY, or to a self-hosted instance URL.
    FIRECRAWL_BASE_URL: str | None = None
    FIRECRAWL_API_KEY: str | None = None

    # Social Media / News APIs
    TWITTER_BEARER_TOKEN: str | None = None
    NEWSAPI_KEY: str | None = None

    # Azure Bot Service
    AZURE_BOT_APP_ID: str | None = None
    AZURE_BOT_APP_PASSWORD: str | None = None
    TEAMS_CHANNEL_ID: str | None = None

    # Teams channel notifications — Power Automate / Workflows incoming webhook.
    # Either anonymous URL (older Logic Apps "When a HTTP request is received"
    # template) or OAuth-protected URL (newer Power Automate "Direct API"
    # template). If OAuth, set the three AZURE_AD_* values below; otherwise
    # leave them blank and the webhook will be called without auth.
    TEAMS_WEBHOOK_URL: str | None = None
    TEAMS_NOTIFICATIONS_ENABLED: bool = True
    TEAMS_NOTIFICATION_MIN_SCORE: int = 1  # importance threshold (LLM scores currently 1-2)

    # Shared secret protecting the RSS feed Power Automate polls.
    # Acts as a password — anyone with the URL+token can read the feed.
    TEAMS_FEED_TOKEN: str = "change-me-please-set-TEAMS_FEED_TOKEN"

    # OAuth (client-credentials) for Power Automate Direct API workflows.
    AZURE_AD_TENANT_ID: str | None = None
    AZURE_AD_CLIENT_ID: str | None = None
    AZURE_AD_CLIENT_SECRET: str | None = None

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
