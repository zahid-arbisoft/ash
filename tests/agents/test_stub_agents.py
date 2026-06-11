import pytest

from ash.agents.fixer import FixerAgent
from ash.agents.reviewer import ReviewerAgent
from ash.config.settings import Settings
from ash.graph.state import WorkflowState


@pytest.mark.parametrize(
    "agent_cls,key",
    [(ReviewerAgent, "reviewer"), (FixerAgent, "fixer")],
)
async def test_stub_agent_annotates_its_namespace(agent_cls, key):
    agent = agent_cls(Settings())
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    update = await agent.run(state)
    assert key in update
    assert update[key]["note"]


async def test_research_skips_without_local_clone(monkeypatch):
    # plane's config has no local clone in CI; research should skip gracefully (not crash)
    from ash.agents.research import ResearchAgent
    from ash.schemas import Epic, Spec, TechnicalSpec

    monkeypatch.delenv("LOCAL_REPO_PATH", raising=False)
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.pm.spec = Spec(
        epic=Epic(title="t", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=[],
    )
    update = await ResearchAgent(Settings()).run(state)
    assert "skipped" in update["research"]["note"]
