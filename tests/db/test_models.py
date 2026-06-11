import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.config.settings import get_settings
from ash.db.base import Base
from ash.db.models import ProviderKind
from ash.integrations.service import create_integration, get_integration, list_integrations


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
    integ = await create_integration(
        session, name="gh", kind=ProviderKind.github, secret="tok-123", config={"repo": "o/r"}
    )
    # transparent decryption on read
    got = await get_integration(session, integ.id)
    assert got is not None
    assert got.secret == "tok-123"
    assert got.config == {"repo": "o/r"}
    # raw column is ciphertext, not the plaintext token
    raw = (await session.execute(text("SELECT secret FROM integrations"))).scalar_one()
    assert raw != "tok-123"
    assert len(raw) > 20


async def test_list_integrations(session):
    await create_integration(session, name="a", kind=ProviderKind.github, secret="x")
    await create_integration(session, name="b", kind=ProviderKind.jira, secret="y")
    names = [i.name for i in await list_integrations(session)]
    assert names == ["a", "b"]
