from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ash.agents.reviewer import ReviewerAgent
from ash.config.settings import Autonomy, ProjectConfig, Settings, WorkTarget
from ash.graph.state import CodingState, WorkflowState
from ash.schemas import (
    CodeChange,
    CodeReview,
    EditAction,
    FileEdit,
    ReviewFinding,
    ReviewSeverity,
    ReviewVerdict,
)


class FakeModel(GenericFakeChatModel):
    """create_agent-compatible fake: emits the structured-output tool call for `result`."""

    def __init__(self, result: BaseModel) -> None:
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": type(result).__name__,
                    "args": result.model_dump(),
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )
        super().__init__(messages=iter([msg]))

    def bind_tools(self, tools, **kwargs):
        return self


def _change() -> CodeChange:
    return CodeChange(
        summary="add endpoint",
        edits=[FileEdit(path="app/api.py", action=EditAction.modify, content="def f(): ...")],
        tests_note="added unit test",
    )


def _state_with_change(pr_url=None) -> WorkflowState:
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.coding = CodingState(change=_change(), pr_url=pr_url, files_written=["app/api.py"])
    return state


async def test_reviewer_approves_clean_change(monkeypatch):
    # hermetic autonomy: human gate on (default), regardless of the dev's plane.yaml
    cfg = ProjectConfig(name="plane", work=WorkTarget(target_repo="o/r"), autonomy=Autonomy())
    monkeypatch.setattr("ash.agents.reviewer.load_project", lambda name: cfg)
    review = CodeReview(summary="looks good", findings=[], verdict=ReviewVerdict.approve)
    agent = ReviewerAgent(Settings(), model=FakeModel(review))
    update = await agent.run(_state_with_change())

    assert update["reviewer"]["verdict"] == "approve"
    assert update["reviewer"]["merged"] is False
    assert "awaiting human merge" in update["reviewer"]["note"]


async def test_reviewer_requests_changes_on_blocking_finding():
    review = CodeReview(
        summary="bug found",
        findings=[
            ReviewFinding(
                path="app/api.py",
                line=1,
                severity=ReviewSeverity.high,
                category="bug",
                comment="null deref",
            )
        ],
        verdict=ReviewVerdict.request_changes,
    )
    agent = ReviewerAgent(Settings(), model=FakeModel(review))
    update = await agent.run(_state_with_change())

    assert update["reviewer"]["verdict"] == "request_changes"
    assert "changes requested" in update["reviewer"]["note"]


async def test_reviewer_auto_merges_when_policy_allows(monkeypatch):
    from ash.config.settings import Settings

    merged_prs: list[str] = []
    monkeypatch.setattr(
        "ash.agents.reviewer.pr_client.review_pr", lambda **kw: None
    )
    monkeypatch.setattr(
        "ash.agents.reviewer.pr_client.merge_pr", lambda **kw: merged_prs.append(kw["pr"])
    )
    # project policy: no human gate + auto-merge on approve
    cfg = ProjectConfig(
        name="plane",
        work=WorkTarget(target_repo="o/r"),
        autonomy=Autonomy(require_human_for_merge=False, auto_merge_on_approve=True),
    )
    monkeypatch.setattr("ash.agents.reviewer.load_project", lambda name: cfg)

    review = CodeReview(summary="great", findings=[], verdict=ReviewVerdict.approve)
    agent = ReviewerAgent(Settings(), model=FakeModel(review))
    update = await agent.run(_state_with_change(pr_url="https://gh/pr/1"))

    assert update["reviewer"]["merged"] is True
    assert merged_prs == ["https://gh/pr/1"]
    assert "auto-merged" in update["reviewer"]["note"]
