"""Async Postgres checkpointer factory.

Uses AsyncConnectionPool so concurrent async tasks (graph execution + HTTP status reads)
each get their own connection, avoiding "another command is already in progress" errors.
`prepare_threshold=0` disables prepared statements which conflict with connection pooling.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# Register all ASH Pydantic models that appear in WorkflowState so LangGraph can
# round-trip them through the Postgres checkpoint store without warnings.
_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        # top-level state container
        ("ash.graph.state", "WorkflowState"),
        # per-agent sub-states
        ("ash.graph.state", "IntakeState"),
        ("ash.graph.state", "PMState"),
        ("ash.graph.state", "RFCState"),
        ("ash.graph.state", "ResearchState"),
        ("ash.graph.state", "DevState"),
        ("ash.graph.state", "ReviewerState"),
        ("ash.graph.state", "FixerState"),
        ("ash.graph.state", "StoryState"),
        # nested value objects — Pydantic models
        ("ash.integrations.base", "RawIssue"),
        ("ash.schemas", "Spec"),
        ("ash.schemas", "Epic"),
        ("ash.schemas", "TechnicalSpec"),
        ("ash.schemas", "Ticket"),
        ("ash.schemas", "Risk"),
        ("ash.schemas", "CodeChange"),
        ("ash.schemas", "FileEdit"),
        ("ash.schemas", "CodeReview"),
        ("ash.schemas", "ReviewFinding"),
        ("ash.schemas", "ImplementationPlan"),
        ("ash.schemas", "RFCDocument"),
        # str Enums used as field values inside the above models
        ("ash.schemas", "TicketType"),
        ("ash.schemas", "Severity"),
        ("ash.schemas", "EditAction"),
        ("ash.schemas", "ReviewSeverity"),
        ("ash.schemas", "ReviewVerdict"),
    ]
)


@asynccontextmanager
async def checkpointer_from_dsn(dsn: str, max_size: int = 20) -> AsyncIterator[Any]:
    """Open an AsyncConnectionPool and yield a thread-safe AsyncPostgresSaver.

    Each concurrent caller (graph task, HTTP handler) gets its own connection from the
    pool, preventing "another command is already in progress" errors from psycopg.
    """
    async with AsyncConnectionPool(
        conninfo=dsn,
        max_size=max_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        yield AsyncPostgresSaver(pool, serde=_SERDE)  # type: ignore[arg-type]
