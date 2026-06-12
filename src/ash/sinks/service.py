"""Task-sink resolution over the unified `connectors` table (explicit → default → file board)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.models import Connector, ConnectorKind
from ash.sinks.base import TicketSink
from ash.sinks.file import FileBoardSink
from ash.sinks.jira import JiraTaskSink
from ash.sinks.plane import PlaneTaskSink


async def get_default_sink(session: AsyncSession) -> Connector | None:
    result = await session.execute(
        select(Connector)
        .where(Connector.is_default_sink, Connector.is_sink, Connector.enabled)
        .limit(1)
    )
    return result.scalar_one_or_none()


def build_sink(connector: Connector, *, board_dir: Path) -> TicketSink:
    """Turn a sink connector into a runtime ticket publisher."""
    if connector.kind == ConnectorKind.file:
        return FileBoardSink(board_dir)
    if connector.kind == ConnectorKind.jira:
        return JiraTaskSink(
            token=connector.secret, config=connector.config or {}, base_url=connector.base_url
        )
    if connector.kind == ConnectorKind.plane:
        return PlaneTaskSink(
            token=connector.secret, config=connector.config or {}, base_url=connector.base_url
        )
    raise NotImplementedError(f"task sink kind not implemented yet: {connector.kind.value}")


async def resolve_task_sink(
    session: AsyncSession, *, sink_id: int | None, board_dir: Path
) -> TicketSink:
    """Pick the sink for a run: explicit connector id → admin default → local file board."""
    row: Connector | None = None
    if sink_id is not None:
        row = await session.get(Connector, sink_id)
    if row is None:
        row = await get_default_sink(session)
    if row is None or not row.enabled or not row.is_sink:
        return FileBoardSink(board_dir)
    return build_sink(row, board_dir=board_dir)
