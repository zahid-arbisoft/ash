"""CRUD for AgentLLMExchange (LLM I/O capture — decision #30).

One row per agent↔LLM exchange (the messages sent + the response received). Written best-effort
from the node wrapper after an agent runs; read back for the per-run "LLM I/O" view.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.models import AgentLLMExchange


async def record_exchanges(
    session: AsyncSession,
    *,
    run_id: str,
    project: str,
    agent_name: str,
    ticket_id: str | None = None,
    exchanges: list[dict[str, Any]],
) -> int:
    """Persist a batch of captured exchanges for one agent run. Returns the count written."""
    count = 0
    for ex in exchanges:
        session.add(
            AgentLLMExchange(
                run_id=run_id,
                project=project,
                ticket_id=ticket_id,
                agent_name=agent_name,
                phase=str(ex.get("phase", "")),
                step=int(ex.get("step", 0)),
                model=str(ex.get("model", "")),
                request=ex.get("request", []),
                response=ex.get("response", {}),
                context=ex.get("context"),
                code=ex.get("code"),
                prompt_tokens=int(ex.get("prompt_tokens", 0)),
                completion_tokens=int(ex.get("completion_tokens", 0)),
            )
        )
        count += 1
    await session.flush()
    return count


async def list_exchanges_for_run(
    session: AsyncSession, run_id: str
) -> list[AgentLLMExchange]:
    """All exchanges for a run, in capture order (id ascending)."""
    rows = await session.execute(
        select(AgentLLMExchange)
        .where(AgentLLMExchange.run_id == run_id)
        .order_by(AgentLLMExchange.id.asc())
    )
    return list(rows.scalars().all())


# Historical agent-name aliasing for the coding→dev rename (decision #33): a filter for "dev"
# also matches legacy rows recorded under "coding".
_AGENT_FILTER_ALIASES = {"dev": ("dev", "coding")}


async def list_exchanges(
    session: AsyncSession,
    *,
    run_id: str | None = None,
    agent: str | None = None,
    ticket: str | None = None,
    phase: str | None = None,
    query: str | None = None,
    limit: int = 500,
) -> list[AgentLLMExchange]:
    """Filtered exchanges across runs (decision #33 / Phase D — the global I/O log).

    Any filter left as None is ignored. `query` matches the model name or agent name
    (case-insensitive substring). Capped at `limit`, newest first."""
    stmt = select(AgentLLMExchange)
    if run_id:
        stmt = stmt.where(AgentLLMExchange.run_id == run_id)
    if agent:
        names = _AGENT_FILTER_ALIASES.get(agent, (agent,))
        stmt = stmt.where(AgentLLMExchange.agent_name.in_(names))
    if ticket:
        stmt = stmt.where(AgentLLMExchange.ticket_id == ticket)
    if phase:
        stmt = stmt.where(AgentLLMExchange.phase == phase)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                AgentLLMExchange.model.ilike(like),
                AgentLLMExchange.agent_name.ilike(like),
                AgentLLMExchange.project.ilike(like),
            )
        )
    # Newest first for the global view; per-run view re-sorts client-side by group.
    order = AgentLLMExchange.id.asc() if run_id else AgentLLMExchange.id.desc()
    stmt = stmt.order_by(order).limit(limit)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())
