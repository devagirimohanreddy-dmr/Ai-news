"""Celery application configuration for the AI News Aggregator Bot.

Start the worker:
    celery -A src.config.celery_app worker --loglevel=info

Start the beat scheduler:
    celery -A src.config.celery_app beat --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab

from src.config.settings import settings

app = Celery("ainews", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "scrape-all-sources": {
            "task": "src.scheduler.scrape_tasks.scrape_all_sources",
            "schedule": crontab(minute="*/30"),  # every 30 minutes
        },
        "generate-daily-digest": {
            "task": "src.scheduler.digest_tasks.generate_daily_digest",
            "schedule": crontab(
                hour=settings.DIGEST_SCHEDULE_HOUR,
                minute=settings.DIGEST_SCHEDULE_MINUTE,
            ),
        },
    },
)

# Auto-discover tasks in the scheduler package so the worker registers them.
app.autodiscover_tasks(["src.scheduler"])
