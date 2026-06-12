"""Logging configuration for ASH.

Call `configure_logging(level)` once at app startup. All other modules obtain
their logger with `logging.getLogger(__name__)` — no other setup needed.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with a consistent format. Safe to call multiple times."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(numeric)
    # Avoid duplicate handlers if called more than once (e.g. in tests)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    # Silence noisy third-party loggers that aren't useful at INFO
    for noisy in ("httpx", "httpcore", "langchain", "langgraph", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
