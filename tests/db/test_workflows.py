import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.db.base import Base
from ash.db.workflows import (
    WORKFLOW_AGENTS,
    clone_workflow,
    create_workflow,
    default_steps,
    default_workflow,
    disable_workflow,
    list_workflows,
    normalize_steps,
    set_default_workflow,
    snapshot_for,
    step_policy,
    update_workflow,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# ── normalize_steps ─────────────────────────────────────────────────────────


def test_default_steps_covers_every_agent_manual():
    steps = default_steps()
    assert [s["agent"] for s in steps] == list(WORKFLOW_AGENTS)
    assert all(s["trigger"] == "manual" and s["enabled"] for s in steps)


def test_normalize_drops_unknown_fills_missing_and_orders():
    raw = [
        {"agent": "dev", "trigger": "auto", "enabled": False},
        {"agent": "bogus", "trigger": "auto"},  # unknown → dropped
        {"agent": "pm", "trigger": "weird"},  # bad trigger → manual
    ]
    out = normalize_steps(raw)
    # canonical order, every agent present exactly once
    assert [s["agent"] for s in out] == list(WORKFLOW_AGENTS)
    by = {s["agent"]: s for s in out}
    assert by["dev"] == {"agent": "dev", "trigger": "auto", "enabled": False}
    assert by["pm"]["trigger"] == "manual"  # coerced
    assert by["research"]["enabled"] is True  # filled default


def test_step_policy_reads_snapshot():
    snap = {"steps": normalize_steps([{"agent": "rfc", "trigger": "auto", "enabled": False}])}
    assert step_policy(snap, "rfc") == {"trigger": "auto", "enabled": False}
    assert step_policy({}, "rfc") is None
    assert step_policy(None, "pm") is None


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def test_create_list_and_snapshot(session):
    wf = await create_workflow(
        session, name="Fast path", steps=[{"agent": "research", "enabled": False}],
        story_execution="one_by_one",
    )
    await session.commit()
    assert wf.version == 1
    rows = await list_workflows(session)
    assert [w.id for w in rows] == [wf.id]
    snap = snapshot_for(wf)
    assert snap["workflow_id"] == wf.id and snap["story_execution"] == "one_by_one"
    assert step_policy(snap, "research")["enabled"] is False


async def test_set_default_is_single(session):
    a = await create_workflow(session, name="A", steps=[], is_default=True)
    b = await create_workflow(session, name="B", steps=[])
    await session.commit()
    assert (await default_workflow(session)).id == a.id
    await set_default_workflow(session, b.id)
    await session.commit()
    assert (await default_workflow(session)).id == b.id
    await session.refresh(a)
    assert a.is_default is False


async def test_update_bumps_version(session):
    wf = await create_workflow(session, name="W", steps=[])
    await session.commit()
    await update_workflow(session, wf.id, name="W2", steps=[{"agent": "dev", "trigger": "auto"}])
    await session.commit()
    await session.refresh(wf)
    assert wf.name == "W2" and wf.version == 2
    assert step_policy(snapshot_for(wf), "dev")["trigger"] == "auto"


async def test_disable_soft_deletes_and_excludes(session):
    wf = await create_workflow(session, name="W", steps=[], is_default=True)
    await session.commit()
    await disable_workflow(session, wf.id)
    await session.commit()
    assert await list_workflows(session) == []  # excluded by default
    assert len(await list_workflows(session, include_disabled=True)) == 1
    assert await default_workflow(session) is None  # disabled can't be default


async def test_clone_duplicates(session):
    src = await create_workflow(
        session, name="Src", steps=[{"agent": "dev", "trigger": "auto"}], story_execution="selected"
    )
    await session.commit()
    copy = await clone_workflow(session, src.id)
    await session.commit()
    assert copy.id != src.id and copy.name == "Src (copy)"
    assert copy.story_execution == "selected" and copy.is_default is False
    assert step_policy(snapshot_for(copy), "dev")["trigger"] == "auto"
