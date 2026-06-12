"""Connector CRUD + issue-source resolution over the unified `connectors` table."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.base import get_sessionmaker
from ash.db.models import Connector, ConnectorKind
from ash.integrations.base import IssueProvider
from ash.integrations.mcp import is_mcp, load_mcp_tools
from ash.integrations.registry import build_provider


async def list_connectors(session: AsyncSession) -> list[Connector]:
    result = await session.execute(select(Connector).order_by(Connector.name))
    return list(result.scalars().all())


async def get_connector(session: AsyncSession, connector_id: int) -> Connector | None:
    return await session.get(Connector, connector_id)


async def create_connector(
    session: AsyncSession,
    *,
    name: str,
    kind: ConnectorKind,
    secret: str = "",
    config: dict[str, Any] | None = None,
    base_url: str | None = None,
    is_source: bool = False,
    is_sink: bool = False,
    is_default_sink: bool = False,
    enabled: bool = True,
) -> Connector:
    connector = Connector(
        name=name,
        kind=kind,
        secret=secret,
        config=config or {},
        base_url=base_url,
        is_source=is_source,
        is_sink=is_sink,
        is_default_sink=is_default_sink,
        enabled=enabled,
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    return connector


async def mcp_tools_for(connector_id: int) -> list[BaseTool]:
    """Load a connector's hosted-MCP tools (empty if it's not an MCP/`transport=http` connector)."""
    async with get_sessionmaker()() as session:
        connector = await get_connector(session, connector_id)
        if connector is None:
            raise LookupError(f"connector {connector_id} not found")
        if not connector.enabled or not is_mcp(connector):
            return []
        return await load_mcp_tools(connector)


async def provider_for(connector_id: int) -> IssueProvider:
    """Open a session, load the connector, and build its issue provider (raises if unusable)."""
    async with get_sessionmaker()() as session:
        connector = await get_connector(session, connector_id)
        if connector is None:
            raise LookupError(f"connector {connector_id} not found")
        if not connector.enabled:
            raise ValueError(f"connector {connector.name!r} is disabled")
        if not connector.is_source:
            raise ValueError(f"connector {connector.name!r} is not enabled as an issue source")
        return build_provider(connector)
