"""structlog JSON logging — call configure_logging once at process start (FastAPI lifespan).

Uses stdlib as the backend so third-party libraries that emit stdlib log records are
captured and rendered through the same JSON pipeline. structlog contextvars (bound via
bind_contextvars) are merged into every log line regardless of whether the call came from
a structlog logger or a stdlib logger.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO") -> None:
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers = [handler]

    for noisy in ("httpx", "httpcore", "langchain", "langgraph", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
