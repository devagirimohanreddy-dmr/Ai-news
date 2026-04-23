"""Structured logging configuration for the AI News Aggregator Bot.

Uses structlog for structured, context-rich logging with JSON output in
production and coloured console output during development.

Usage:
    from src.config.logging import setup_logging
    setup_logging(log_level="INFO")

    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("article.processed", article_id=42, score=8)
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog


def _add_correlation_id(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Add a correlation_id to the event dict if one is not already present.

    Each log entry receives a unique trace ID so a single article's journey
    through the pipeline (ingest -> dedup -> classify -> score -> summarize ->
    route) can be correlated in log aggregation tools.
    """
    if "correlation_id" not in event_dict:
        event_dict["correlation_id"] = str(uuid.uuid4())
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
                   Defaults to INFO.  Typically sourced from ``settings.LOG_LEVEL``.

    Behaviour:
        * **Production** (non-TTY stdout): JSON renderer for machine-readable logs.
        * **Development** (TTY stdout): Coloured console renderer for readability.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Decide renderer based on whether we're attached to a real terminal.
    is_interactive = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    if is_interactive:
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    # Shared processors applied to every log event.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog itself.
    structlog.configure(
        processors=[
            *shared_processors,
            # Prepare for the final renderer.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Create a formatter that structlog-aware handlers use.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Wire up Python's standard logging so *all* loggers (third-party included)
    # flow through structlog's pipeline.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Quiet down noisy third-party loggers.
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.get_logger("src.config.logging").info(
        "logging.configured",
        log_level=log_level,
        renderer="console" if is_interactive else "json",
    )
