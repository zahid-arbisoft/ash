"""Async Postgres checkpointer factory.

`AsyncPostgresSaver.from_conn_string(dsn)` returns an async context manager yielding the saver.
On a fresh DB, call `await saver.setup()` once (done in the API lifespan) to create the tables.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def checkpointer_from_dsn(dsn: str) -> Any:
    return AsyncPostgresSaver.from_conn_string(dsn)
