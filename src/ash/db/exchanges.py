"""CRUD for AgentLLMExchange (LLM I/O capture — decision #30).

One row per agent↔LLM exchange (the messages sent + the response received). Written best-effort
from the node wrapper after an agent runs; read back for the per-run "LLM I/O" view.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
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
