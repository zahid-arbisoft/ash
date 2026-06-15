"""CRUD + aggregations for AgentRunMetric (analytics — decision #26 / F8).

One row per agent execution captures tokens (in/out), wall-clock duration, and model. Aggregation
helpers roll these up by run, story, agent, and project for the UI.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.models import AgentRunMetric


async def record_metric(
    session: AsyncSession,
    *,
    run_id: str,
    project: str,
    agent_name: str,
    ticket_id: str | None = None,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: int = 0,
    status: str = "completed",
) -> AgentRunMetric:
    metric = AgentRunMetric(
        run_id=run_id,
        project=project,
        ticket_id=ticket_id,
        agent_name=agent_name,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        duration_ms=duration_ms,
        status=status,
    )
    session.add(metric)
    await session.flush()
    return metric


def _row_to_dict(row: Any) -> dict[str, int]:
    return {
        "prompt_tokens": int(row.prompt or 0),
        "completion_tokens": int(row.completion or 0),
        "total_tokens": int(row.total or 0),
        "duration_ms": int(row.duration or 0),
        "runs": int(row.runs or 0),
    }


async def run_totals(session: AsyncSession, run_id: str) -> dict[str, int]:
    """Sum of tokens + duration across all agents for one run."""
    row = (
        await session.execute(
            select(
                func.sum(AgentRunMetric.prompt_tokens).label("prompt"),
                func.sum(AgentRunMetric.completion_tokens).label("completion"),
                func.sum(AgentRunMetric.total_tokens).label("total"),
                func.sum(AgentRunMetric.duration_ms).label("duration"),
                func.count().label("runs"),
            ).where(AgentRunMetric.run_id == run_id)
        )
    ).one()
    return _row_to_dict(row)


async def run_breakdown(
    session: AsyncSession, run_id: str
) -> dict[tuple[str, str], dict[str, int]]:
    """Per (ticket_id, agent_name) totals for a run — drives the per-story/stage chips.

    Key is (ticket_id or "", agent_name). Sums repeated executions (retries/regenerates).
    """
    rows = await session.execute(
        select(
            AgentRunMetric.ticket_id,
            AgentRunMetric.agent_name,
            func.sum(AgentRunMetric.prompt_tokens).label("prompt"),
            func.sum(AgentRunMetric.completion_tokens).label("completion"),
            func.sum(AgentRunMetric.total_tokens).label("total"),
            func.sum(AgentRunMetric.duration_ms).label("duration"),
            func.count().label("runs"),
        )
        .where(AgentRunMetric.run_id == run_id)
        .group_by(AgentRunMetric.ticket_id, AgentRunMetric.agent_name)
    )
    out: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        out[(r.ticket_id or "", r.agent_name)] = _row_to_dict(r)
    return out


async def agent_rollup(
    session: AsyncSession, *, project: str | None = None, days: int | None = None
) -> list[dict[str, Any]]:
    """Per-agent rollups across runs (avg + total tokens/time), for the Agents view."""
    q = select(
        AgentRunMetric.agent_name,
        func.sum(AgentRunMetric.prompt_tokens).label("prompt"),
        func.sum(AgentRunMetric.completion_tokens).label("completion"),
        func.sum(AgentRunMetric.total_tokens).label("total"),
        func.sum(AgentRunMetric.duration_ms).label("duration"),
        func.avg(AgentRunMetric.total_tokens).label("avg_tokens"),
        func.avg(AgentRunMetric.duration_ms).label("avg_duration"),
        func.count().label("runs"),
    ).group_by(AgentRunMetric.agent_name)
    if project:
        q = q.where(AgentRunMetric.project == project)
    if days:
        since = datetime.now(UTC) - timedelta(days=days)
        q = q.where(AgentRunMetric.created_at >= since)
    rows = await session.execute(q)
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "agent_name": r.agent_name,
                "prompt_tokens": int(r.prompt or 0),
                "completion_tokens": int(r.completion or 0),
                "total_tokens": int(r.total or 0),
                "duration_ms": int(r.duration or 0),
                "avg_tokens": int(r.avg_tokens or 0),
                "avg_duration_ms": int(r.avg_duration or 0),
                "runs": int(r.runs or 0),
            }
        )
    return out


async def project_window_totals(
    session: AsyncSession, *, project: str | None = None, days: int = 7
) -> dict[str, int]:
    """Tokens + duration over the last `days` (dashboard KPI)."""
    since = datetime.now(UTC) - timedelta(days=days)
    q = select(
        func.sum(AgentRunMetric.prompt_tokens).label("prompt"),
        func.sum(AgentRunMetric.completion_tokens).label("completion"),
        func.sum(AgentRunMetric.total_tokens).label("total"),
        func.sum(AgentRunMetric.duration_ms).label("duration"),
        func.count().label("runs"),
    ).where(AgentRunMetric.created_at >= since)
    if project:
        q = q.where(AgentRunMetric.project == project)
    row = (await session.execute(q)).one()
    return _row_to_dict(row)
