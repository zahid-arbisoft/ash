"""CRUD + provider resolution over the `integrations` table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.base import get_sessionmaker
from ash.db.models import Integration, ProviderKind
from ash.integrations.base import IssueProvider
from ash.integrations.registry import build_provider


async def list_integrations(session: AsyncSession) -> list[Integration]:
    result = await session.execute(select(Integration).order_by(Integration.name))
    return list(result.scalars().all())


async def get_integration(session: AsyncSession, integration_id: int) -> Integration | None:
    return await session.get(Integration, integration_id)


async def create_integration(
    session: AsyncSession,
    *,
    name: str,
    kind: ProviderKind,
    secret: str,
    config: dict[str, Any] | None = None,
    base_url: str | None = None,
    enabled: bool = True,
) -> Integration:
    integration = Integration(
        name=name,
        kind=kind,
        secret=secret,
        config=config or {},
        base_url=base_url,
        enabled=enabled,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def provider_for(integration_id: int) -> IssueProvider:
    """Open a session, load the integration, and build its provider (raises if missing/disabled)."""
    async with get_sessionmaker()() as session:
        integration = await get_integration(session, integration_id)
        if integration is None:
            raise LookupError(f"integration {integration_id} not found")
        if not integration.enabled:
            raise ValueError(f"integration {integration.name!r} is disabled")
        return build_provider(integration)
