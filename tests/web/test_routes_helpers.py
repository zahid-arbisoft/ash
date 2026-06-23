import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.config.settings import KNOWN_AGENTS, get_settings
from ash.db.base import Base
from ash.web.routes import (
    _agent_rows,
    _live_stage,
    _run_stage_status,
    _steps_from_form,
    _story_stage_status,
    _sse,
    _wf_disabled,
)


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


# ── workflow-disabled agents surface as skipped in the rail ─────────────────────

_SNAP = {
    "workflow_snapshot": {
        "steps": [
            {"agent": "rfc", "trigger": "manual", "enabled": False},
            {"agent": "research", "trigger": "manual", "enabled": False},
            {"agent": "dev", "trigger": "manual", "enabled": True},
        ]
    }
}


def test_wf_disabled_reads_snapshot():
    assert _wf_disabled(_SNAP, "rfc") is True
    assert _wf_disabled(_SNAP, "dev") is False
    assert _wf_disabled(_SNAP, "pm") is False  # absent → enabled
    assert _wf_disabled({}, "rfc") is False


def test_run_stage_status_skipped_when_workflow_disables():
    state = {**_SNAP, "status": "running", "rfc": {}}
    assert _run_stage_status(state, "rfc") == "skipped"
    # but an error still wins over the skipped shortcut
    state_err = {**_SNAP, "status": "running", "rfc": {"error": "boom"}}
    assert _run_stage_status(state_err, "rfc") == "failed"


def test_story_stage_status_skipped_when_workflow_disables():
    story = {"ticket_id": "T1", "research": {}, "dev": {}}
    assert _story_stage_status(_SNAP, story, "research") == "skipped"
    assert _story_stage_status(_SNAP, story, "dev") == "pending"


# ── workflow builder form parsing ──────────────────────────────────────────────


def test_steps_from_form_reads_per_agent_fields():
    form = {
        "enabled_pm": "on", "trigger_pm": "auto",
        "enabled_dev": "on", "trigger_dev": "manual",
        # research omitted enabled → disabled
        "trigger_research": "manual",
    }
    steps = _steps_from_form(form)
    by = {s["agent"]: s for s in steps}
    assert by["pm"] == {"agent": "pm", "enabled": True, "trigger": "auto"}
    assert by["dev"]["enabled"] is True and by["dev"]["trigger"] == "manual"
    assert by["research"]["enabled"] is False  # checkbox absent → off
    # every gateable agent is represented
    from ash.db.workflows import WORKFLOW_AGENTS
    assert set(by) == set(WORKFLOW_AGENTS)


def test_steps_from_form_honours_order_and_appends_missing():
    form = {"order": "dev,pm", "enabled_dev": "on", "enabled_pm": "on"}
    agents = [s["agent"] for s in _steps_from_form(form)]
    assert agents[:2] == ["dev", "pm"]  # authored order respected
    from ash.db.workflows import WORKFLOW_AGENTS
    assert set(agents) == set(WORKFLOW_AGENTS)  # rest appended


# ── live-stage derivation (graph/story, not the AgentTask table) ────────────────


def _story(**ns) -> dict:
    base = {"ticket_id": "T1", "research": {}, "dev": {}, "reviewer": {}, "fixer": {}}
    base.update(ns)
    return base


def test_live_stage_first_incomplete_build_step():
    # research done (has plan) → the running step is dev
    state = {"current_story": "T1", "stories": {"T1": _story(research={"plan": {"summary": "x"}})}}
    assert _live_stage(state) == ("dev", "T1")
    # nothing done yet → research is the running step
    state2 = {"current_story": "T1", "stories": {"T1": _story()}}
    assert _live_stage(state2) == ("research", "T1")


def test_live_stage_skips_workflow_disabled_step():
    # research disabled by workflow → it's skipped, so dev is the running step
    state = {
        "current_story": "T1",
        "stories": {"T1": _story()},
        "workflow_snapshot": {"steps": [{"agent": "research", "enabled": False}]},
    }
    assert _live_stage(state) == ("dev", "T1")


def test_live_stage_run_level_from_next_node():
    assert _live_stage({"current_story": "", "_next_nodes": ["pm"]}) == ("pm", "")
    assert _live_stage({"current_story": "", "_next_nodes": ["pm_publish"]}) == ("pm", "")
    # between stories / planning → no run-level stage
    assert _live_stage({"current_story": "", "_next_nodes": ["story_router"]}) == (None, "")


def test_live_stage_awaiting_trigger_is_not_running():
    # the story's research is gated (awaiting trigger) → not "running"
    state = {
        "current_story": "T1",
        "stories": {"T1": _story()},
        "pending_trigger": "research",
        "pending_story": "T1",
    }
    assert _live_stage(state) == (None, "")
