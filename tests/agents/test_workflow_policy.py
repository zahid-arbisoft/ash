"""Precedence of policy resolution with the workflow layer (workflow-builder).

Order: AgentPolicyRecord (Agents page) > run's workflow snapshot > projects/*.yaml > default.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.agents.base import BaseAgent
from ash.config.settings import AgentPolicy, ProjectConfig, Settings
from ash.db.base import Base
from ash.db.tasks import upsert_policy_override
from ash.db.workflows import normalize_steps
from ash.graph.state import WorkflowState


class _ResearchAgent(BaseAgent):
    name = "research"

    async def run(self, state):  # pragma: no cover — not exercised
        return {}


@pytest_asyncio.fixture
async def maker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("ash.db.base.get_sessionmaker", lambda: m)
    yield m
    await engine.dispose()


def _state(snapshot: dict | None = None) -> WorkflowState:
    s = WorkflowState(run_id="r1", project="plane", item_id="1")
    if snapshot is not None:
        s.workflow_snapshot = snapshot
    return s


def _snap(**overrides) -> dict:
    # research disabled by default in these snapshots unless overridden
    steps = normalize_steps([{"agent": "research", **overrides}])
    return {"workflow_id": 1, "steps": steps}


async def test_workflow_overrides_yaml(monkeypatch, maker):
    # YAML says research auto+enabled; workflow says disabled → workflow wins (no DB override).
    cfg = ProjectConfig(name="plane", agents={"research": AgentPolicy(trigger="auto")})
    monkeypatch.setattr("ash.agents.base.load_project", lambda n: cfg)
    resolved = await _ResearchAgent(Settings())._resolve_policy(
        _state(_snap(enabled=False, trigger="auto"))
    )
    assert resolved is not None
    _project, policy = resolved
    assert policy.enabled is False  # workflow disabled it over the YAML enable


async def test_agents_page_override_beats_workflow(monkeypatch, maker):
    # DB AgentPolicyRecord (Agents page) sets research manual+enabled; workflow says auto+disabled.
    cfg = ProjectConfig(name="plane")
    monkeypatch.setattr("ash.agents.base.load_project", lambda n: cfg)
    async with maker() as s:
        await upsert_policy_override(s, "plane", "research", trigger="manual", enabled=True)
        await s.commit()
    resolved = await _ResearchAgent(Settings())._resolve_policy(
        _state(_snap(enabled=False, trigger="auto"))
    )
    assert resolved is not None
    _project, policy = resolved
    assert policy.trigger == "manual" and policy.enabled is True  # DB wins over workflow


async def test_no_workflow_falls_back_to_yaml(monkeypatch, maker):
    cfg = ProjectConfig(name="plane", agents={"research": AgentPolicy(trigger="auto")})
    monkeypatch.setattr("ash.agents.base.load_project", lambda n: cfg)
    resolved = await _ResearchAgent(Settings())._resolve_policy(_state())  # empty snapshot
    assert resolved is not None
    _project, policy = resolved
    assert policy.trigger == "auto"  # YAML
