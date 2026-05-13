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
            # Tick every 15 minutes — the dispatcher then filters each
            # source by its own ``schedule_cron`` so a source set to
            # "every 2h" only runs every 8 ticks, etc. 15 min is our
            # finest configured schedule (Hacker News).
            "schedule": crontab(minute="*/15"),
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

# Explicitly import task modules so the worker registers them.
import src.scheduler.scrape_tasks  # noqa: F401, E402
import src.scheduler.digest_tasks  # noqa: F401, E402
import src.scheduler.alert_tasks   # noqa: F401, E402

from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def reset_db_engine(**kwargs):
    """Reset the SQLAlchemy async engine in each forked worker process.

    Celery uses prefork workers. Forked processes inherit the parent's
    asyncpg connection pool, which is bound to the parent's event loop.
    Resetting the globals forces each worker to create a fresh engine and
    pool tied to its own event loop.
    """
    import src.models.base as base_module
    base_module._engine = None
    base_module._async_session = None
