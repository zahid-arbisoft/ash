"""Async SQLAlchemy engine/session wiring.

Reuses the same Postgres instance as the checkpointer (DSN from `Settings`), driven by psycopg3 in
async mode (`postgresql+psycopg://`). Tables are created on startup via `init_db()` (Alembic
migrations are a later hardening step). The engine/sessionmaker are lazy so importing this module
never opens a connection — important for offline tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
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


# Lightweight, idempotent column backfills for tables that predate a schema change. This is a
# stopgap until Alembic: `create_all` creates *missing tables* but never ALTERs existing ones, so a
# DB created before a column was added would 500 on the new SELECT. Postgres-only (the app DB);
# `ADD COLUMN IF NOT EXISTS` makes each statement safe to run on every startup.
_PG_COLUMN_BACKFILLS: tuple[str, ...] = (
    "ALTER TABLE run_records ADD COLUMN IF NOT EXISTS task_sink_id INTEGER",
    "ALTER TABLE run_records ADD COLUMN IF NOT EXISTS status VARCHAR(40) "
    "NOT NULL DEFAULT 'running'",
    "ALTER TABLE run_records ADD COLUMN IF NOT EXISTS ticket_id VARCHAR(120) "
    "NOT NULL DEFAULT ''",
    "ALTER TABLE run_records ADD COLUMN IF NOT EXISTS story_mode VARCHAR(20) "
    "NOT NULL DEFAULT 'single'",
    # decision #26: per-story task scoping (agent_tasks predates the ticket_id column).
    "ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS ticket_id VARCHAR(120) "
    "NOT NULL DEFAULT ''",
    # story_records / agent_run_metrics are created fresh by create_all; no backfills needed.
)


async def _backfill_columns(conn: AsyncConnection) -> None:
    if conn.dialect.name != "postgresql":
        return  # only the Postgres app DB needs these; sqlite test DBs are built fresh
    for stmt in _PG_COLUMN_BACKFILLS:
        await conn.execute(text(stmt))


async def init_db() -> None:
    """Create app tables if they don't exist, then backfill new columns on pre-existing tables."""
    import ash.db.models  # noqa: F401 — register tables on Base.metadata

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _backfill_columns(conn)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
