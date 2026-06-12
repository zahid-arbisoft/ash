import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.config.settings import get_settings
from ash.db.base import Base
from ash.db.models import ConnectorKind
from ash.integrations.service import create_connector, get_connector, list_connectors


@pytest_asyncio.fixture
async def session(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
    get_settings.cache_clear()


async def test_secret_encrypted_at_rest_and_decrypts(session):
    conn = await create_connector(
        session,
        name="gh",
        kind=ConnectorKind.github,
        secret="tok-123",
        config={"repo": "o/r"},
        is_source=True,
    )
    got = await get_connector(session, conn.id)
    assert got is not None
    assert got.secret == "tok-123"  # transparent decryption
    assert got.config == {"repo": "o/r"}
    assert got.is_source and not got.is_sink
    # raw column is ciphertext, not the plaintext token
    raw = (await session.execute(text("SELECT secret FROM connectors"))).scalar_one()
    assert raw != "tok-123"
    assert len(raw) > 20


async def test_connector_can_be_both_source_and_sink(session):
    await create_connector(session, name="a", kind=ConnectorKind.github, secret="x", is_source=True)
    jira = await create_connector(
        session, name="b", kind=ConnectorKind.jira, secret="y", is_source=True, is_sink=True
    )
    assert jira.is_source and jira.is_sink
    names = [c.name for c in await list_connectors(session)]
    assert names == ["a", "b"]
