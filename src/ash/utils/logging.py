"""Logging configuration — call once at process start (FastAPI lifespan)."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers = [handler]

    for noisy in ("httpx", "httpcore", "langchain", "langgraph", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
