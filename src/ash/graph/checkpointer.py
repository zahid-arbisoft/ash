"""Async Postgres checkpointer factory.

`AsyncPostgresSaver.from_conn_string(dsn)` returns an async context manager yielding the saver.
On a fresh DB, call `await saver.setup()` once (done in the API lifespan) to create the tables.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

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
        ("ash.graph.state", "CodingState"),
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


def checkpointer_from_dsn(dsn: str) -> Any:
    return AsyncPostgresSaver.from_conn_string(dsn, serde=_SERDE)
