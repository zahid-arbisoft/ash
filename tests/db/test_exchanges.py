"""Tests for AgentLLMExchange persistence (decision #30)."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.db.base import Base
from ash.db.exchanges import list_exchanges_for_run, record_exchanges


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


def _exchanges():
    return [
        {
            "phase": "single_call",
            "step": 0,
            "model": "qwen",
            "request": [
                {"role": "system", "content": "you are PM"},
                {"role": "user", "content": "spec"},
            ],
            "response": {"content": "", "parsed": {"epic": {"title": "X"}}},
            "prompt_tokens": 100,
            "completion_tokens": 40,
        },
        {
            "phase": "extract",
            "step": 0,
            "model": "qwen",
            "request": [{"role": "user", "content": "notes"}],
            "response": {"parsed": {"id": "T1"}},
            "prompt_tokens": 20,
            "completion_tokens": 10,
        },
    ]


async def test_record_and_list_round_trip(maker):
    async with maker() as s:
        n = await record_exchanges(
            s, run_id="r1", project="plane", agent_name="pm", ticket_id=None,
            exchanges=_exchanges(),
        )
        await s.commit()
        assert n == 2

    async with maker() as s:
        rows = await list_exchanges_for_run(s, "r1")
        assert [r.phase for r in rows] == ["single_call", "extract"]  # id-ascending order
        assert rows[0].agent_name == "pm"
        assert rows[0].prompt_tokens == 100
        assert rows[0].request[0]["role"] == "system"
        assert rows[0].response["parsed"]["epic"]["title"] == "X"
        # a different run sees nothing
        assert await list_exchanges_for_run(s, "other") == []


async def test_empty_batch_writes_nothing(maker):
    async with maker() as s:
        n = await record_exchanges(
            s, run_id="r2", project="p", agent_name="pm", exchanges=[]
        )
        await s.commit()
        assert n == 0
        assert await list_exchanges_for_run(s, "r2") == []
