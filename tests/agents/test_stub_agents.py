import pytest

from ash.agents.fixer import FixerAgent
from ash.agents.reviewer import ReviewerAgent
from ash.config.settings import Settings
from ash.graph.state import WorkflowState


@pytest.mark.parametrize(
    "agent_cls,key",
    [(ReviewerAgent, "reviewer"), (FixerAgent, "fixer")],
)
async def test_agent_skips_gracefully_with_no_work(agent_cls, key):
    """With nothing to review/fix, each agent annotates a skip note instead of crashing."""
    agent = agent_cls(Settings())
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    update = await agent.run(state)
    assert key in update
    assert "skipped" in update[key]["note"]


async def test_research_skips_without_local_clone(monkeypatch):
    # research should skip gracefully (not crash) when the project has no local clone.
    # Hermetic: force a project config with no local_repo_path, regardless of the dev's plane.yaml.
    from ash.agents.research import ResearchAgent
    from ash.config.settings import IssueSource, ProjectConfig, WorkTarget
    from ash.schemas import Epic, Spec, TechnicalSpec

    cfg = ProjectConfig(
        name="plane",
        issues=IssueSource(source_repo="o/r"),
        work=WorkTarget(target_repo="o/r"),  # local_repo_path defaults to None
    )
    monkeypatch.setattr("ash.agents.research.load_project", lambda name: cfg)

    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.pm.spec = Spec(
        epic=Epic(title="t", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=[],
    )
    update = await ResearchAgent(Settings()).run(state)
    assert "skipped" in update["research"]["note"]
