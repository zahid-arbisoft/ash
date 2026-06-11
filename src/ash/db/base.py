"""Async SQLAlchemy engine/session wiring.

Reuses the same Postgres instance as the checkpointer (DSN from `Settings`), driven by psycopg3 in
async mode (`postgresql+psycopg://`). Tables are created on startup via `init_db()` (Alembic
migrations are a later hardening step). The engine/sessionmaker are lazy so importing this module
never opens a connection — important for offline tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ash.config.settings import get_settings


class Base(DeclarativeBase):
    pass


def async_dsn(dsn: str) -> str:
    """Normalize a libpq DSN to the SQLAlchemy async psycopg driver."""
    if dsn.startswith("postgresql+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+psycopg://", 1)
    return dsn


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(async_dsn(get_settings().postgres_dsn), pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def init_db() -> None:
    """Create app tables if they don't exist (import models first so they register)."""
    import ash.db.models  # noqa: F401 — register tables on Base.metadata

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
