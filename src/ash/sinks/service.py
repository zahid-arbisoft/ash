"""Task-sink CRUD + run-time resolution (explicit → default → file board)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.models import SinkKind, TaskSink
from ash.sinks.base import TicketSink
from ash.sinks.file import FileBoardSink
from ash.sinks.jira import JiraTaskSink
from ash.sinks.plane import PlaneTaskSink


async def list_task_sinks(session: AsyncSession) -> list[TaskSink]:
    result = await session.execute(select(TaskSink).order_by(TaskSink.name))
    return list(result.scalars().all())


async def get_default_sink(session: AsyncSession) -> TaskSink | None:
    result = await session.execute(
        select(TaskSink).where(TaskSink.is_default, TaskSink.enabled).limit(1)
    )
    return result.scalar_one_or_none()


async def create_task_sink(
    session: AsyncSession,
    *,
    name: str,
    kind: SinkKind,
    secret: str = "",
    config: dict[str, Any] | None = None,
    base_url: str | None = None,
    is_default: bool = False,
    enabled: bool = True,
) -> TaskSink:
    sink = TaskSink(
        name=name,
        kind=kind,
        secret=secret,
        config=config or {},
        base_url=base_url,
        is_default=is_default,
        enabled=enabled,
    )
    session.add(sink)
    await session.commit()
    await session.refresh(sink)
    return sink


def build_sink(row: TaskSink, *, board_dir: Path) -> TicketSink:
    """Turn a TaskSink row into a runtime publisher."""
    if row.kind == SinkKind.file:
        return FileBoardSink(board_dir)
    if row.kind == SinkKind.jira:
        return JiraTaskSink(token=row.secret, config=row.config or {}, base_url=row.base_url)
    if row.kind == SinkKind.plane:
        return PlaneTaskSink(token=row.secret, config=row.config or {}, base_url=row.base_url)
    raise NotImplementedError(f"task sink kind not implemented yet: {row.kind.value}")


async def resolve_task_sink(
    session: AsyncSession, *, sink_id: int | None, board_dir: Path
) -> TicketSink:
    """Pick the sink for a run: explicit id → admin default → local file board."""
    row: TaskSink | None = None
    if sink_id is not None:
        row = await session.get(TaskSink, sink_id)
    if row is None:
        row = await get_default_sink(session)
    if row is None or not row.enabled:
        return FileBoardSink(board_dir)
    return build_sink(row, board_dir=board_dir)
