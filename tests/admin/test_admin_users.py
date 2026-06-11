import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.admin.security import hash_password, verify_password
from ash.admin.users import authenticate, create_or_update_admin, get_admin_user
from ash.config.settings import get_settings
from ash.db.base import Base


def test_hash_roundtrip():
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


def test_verify_rejects_garbage():
    assert not verify_password("x", "not-a-valid-hash")


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


async def test_create_and_authenticate(session):
    await create_or_update_admin(session, username="alice", password="pw1")
    assert await authenticate(session, "alice", "pw1")
    assert not await authenticate(session, "alice", "nope")
    assert not await authenticate(session, "ghost", "pw1")


async def test_create_is_idempotent_and_resets_password(session):
    await create_or_update_admin(session, username="bob", password="old")
    await create_or_update_admin(session, username="bob", password="new")
    user = await get_admin_user(session, "bob")
    assert user is not None
    assert await authenticate(session, "bob", "new")
    assert not await authenticate(session, "bob", "old")
