from ash.agents.intake import IntakeAgent
from ash.config.settings import Settings
from ash.graph.state import WorkflowState


async def test_spec_file_mode_skips_issue_fetch():
    """Intake must not call any provider when mode is spec_file."""

    class ShouldNotBeCalled:
        async def fetch_issue(self, item_id: str) -> None:
            raise AssertionError("fetch_issue must not be called in spec_file mode")

    agent = IntakeAgent(Settings(), provider=ShouldNotBeCalled())  # type: ignore[arg-type]
    state = WorkflowState(
        run_id="r1",
        project="plane",
        item_id="",
        intake_mode="spec_file",
        spec_file_path="plane/specs/test.md",
    )
    update = await agent.run(state)

    assert update["intake"]["note"] == "spec_file mode — no issue fetch"
    assert "raw_issue" not in update
