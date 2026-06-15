"""CRUD helpers for StoryRecord (per-story persistence — decision #26 / F2).

Survives restarts so the no-duplicate-PR check and the per-story UI work without loading the
checkpoint. Upserted by (run_id, ticket_id) as a story advances.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.models import StoryRecord


async def get_story(session: AsyncSession, run_id: str, ticket_id: str) -> StoryRecord | None:
    result = await session.execute(
        select(StoryRecord).where(
            StoryRecord.run_id == run_id, StoryRecord.ticket_id == ticket_id
        )
    )
    return result.scalar_one_or_none()


async def upsert_story(
    session: AsyncSession,
    *,
    run_id: str,
    ticket_id: str,
    project: str,
    **fields: Any,
) -> StoryRecord:
    record = await get_story(session, run_id, ticket_id)
    if record is None:
        record = StoryRecord(run_id=run_id, ticket_id=ticket_id, project=project)
        session.add(record)
    for k, v in fields.items():
        if v is not None:
            setattr(record, k, v)
    await session.flush()
    return record


async def list_stories_for_run(session: AsyncSession, run_id: str) -> list[StoryRecord]:
    result = await session.execute(
        select(StoryRecord)
        .where(StoryRecord.run_id == run_id)
        .order_by(StoryRecord.position, StoryRecord.created_at)
    )
    return list(result.scalars())
