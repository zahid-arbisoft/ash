import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.config.settings import get_settings
from ash.db.base import Base
from ash.db.models import SinkKind
from ash.sinks.file import FileBoardSink
from ash.sinks.jira import JiraTaskSink
from ash.sinks.service import create_task_sink, resolve_task_sink


@pytest_asyncio.fixture
async def session(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()
    get_settings.cache_clear()


async def test_no_sinks_falls_back_to_file_board(session, tmp_path):
    sink = await resolve_task_sink(session, sink_id=None, board_dir=tmp_path)
    assert isinstance(sink, FileBoardSink)


async def test_default_sink_is_used_when_no_explicit_choice(session, tmp_path):
    await create_task_sink(
        session,
        name="jira-default",
        kind=SinkKind.jira,
        secret="tok",
        config={"email": "e@x.com", "project_key": "ENG"},
        base_url="https://x.atlassian.net",
        is_default=True,
    )
    sink = await resolve_task_sink(session, sink_id=None, board_dir=tmp_path)
    assert isinstance(sink, JiraTaskSink)


async def test_explicit_choice_overrides_default(session, tmp_path):
    await create_task_sink(
        session,
        name="jira-default",
        kind=SinkKind.jira,
        secret="t",
        config={"project_key": "ENG"},
        base_url="https://x.atlassian.net",
        is_default=True,
    )
    file_sink = await create_task_sink(session, name="local", kind=SinkKind.file)
    sink = await resolve_task_sink(session, sink_id=file_sink.id, board_dir=tmp_path)
    assert isinstance(sink, FileBoardSink)
