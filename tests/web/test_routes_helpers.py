import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.config.settings import KNOWN_AGENTS, get_settings
from ash.db.base import Base
from ash.web.routes import _agent_rows, _sse


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


def test_sse_formats_multiline_with_event():
    out = _sse("line1\nline2", event="message")
    assert out == "event: message\ndata: line1\ndata: line2\n\n"


def test_sse_no_event():
    assert _sse("hi") == "data: hi\n\n"


async def test_agent_rows_lists_all_known_agents_with_build_status(session):
    rows = await _agent_rows("plane", session)  # plane.yaml exists in the repo
    assert [r["name"] for r in rows] == list(KNOWN_AGENTS)
    by_name = {r["name"]: r for r in rows}
    # built agents expose a model
    assert by_name["pm"]["built"] is True
    assert by_name["pm"]["model"] != "—"
    assert by_name["rfc"]["built"] is True
    assert by_name["rfc"]["model"] != "—"
    # reviewer surfaces the merge HITL flag; others don't
    assert by_name["reviewer"]["hitl"] in (True, False)
    assert by_name["pm"]["hitl"] is None
    # every row has a trigger mode
    assert all(r["trigger"] in ("auto", "manual") for r in rows)


async def test_agent_rows_unknown_project_is_empty(session):
    assert await _agent_rows("does-not-exist", session) == []
