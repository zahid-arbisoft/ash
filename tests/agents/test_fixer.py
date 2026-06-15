from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ash.agents.fixer import FixerAgent
from ash.config.settings import ProjectConfig, Settings, WorkTarget
from ash.graph.state import CodingState, ResearchState, ReviewerState, WorkflowState
from ash.schemas import (
    CodeChange,
    CodeReview,
    EditAction,
    FileEdit,
    ImplementationPlan,
    ReviewFinding,
    ReviewSeverity,
    ReviewVerdict,
)


class FakeModel(GenericFakeChatModel):
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


class _FakeWorkspace:
    def __init__(self, work, root):
        self.commits: list[str] = []
        self.pushes: list[str] = []

    def commit_all(self, wt, message):
        self.commits.append(message)
        return "deadbeef"

    def push_branch(self, wt, branch, *, force=False):
        self.pushes.append(branch)


def _review_requesting_changes() -> CodeReview:
    return CodeReview(
        summary="needs work",
        findings=[
            ReviewFinding(
                path="app/api.py",
                line=2,
                severity=ReviewSeverity.high,
                category="bug",
                comment="handle None",
                suggestion="add a guard",
            )
        ],
        verdict=ReviewVerdict.request_changes,
    )


def _state(tmp_path) -> WorkflowState:
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.coding = CodingState(
        change=CodeChange(
            summary="initial",
            edits=[FileEdit(path="app/api.py", action=EditAction.modify, content="x = 1")],
        ),
        pr_url=None,
    )
    state.reviewer = ReviewerState(review=_review_requesting_changes(), verdict="request_changes")
    state.research = ResearchState(
        plan=ImplementationPlan(summary="s"),
        branch="agent/fix-42",
        worktree_path=str(tmp_path),
    )
    return state


async def test_fixer_skips_when_review_approved():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.reviewer = ReviewerState(
        review=CodeReview(summary="ok", findings=[], verdict=ReviewVerdict.approve)
    )
    update = await FixerAgent(Settings()).run(state)
    assert "skipped" in update["fixer"]["note"]


async def test_fixer_applies_fix_commits_and_pushes(monkeypatch, tmp_path):
    fixed = CodeChange(
        summary="add None guard",
        edits=[
            FileEdit(
                path="app/api.py",
                action=EditAction.modify,
                content="x = 1\nif x is None:\n    raise ValueError",
            )
        ],
    )
    ws_holder: dict = {}

    def _make_ws(work, root, **kw):
        ws = _FakeWorkspace(work, root)
        ws_holder["ws"] = ws
        return ws

    monkeypatch.setattr("ash.agents.fixer.RepoWorkspace", _make_ws)
    cfg = ProjectConfig(name="plane", work=WorkTarget(target_repo="o/r"))
    monkeypatch.setattr("ash.agents.fixer.load_project", lambda name: cfg)

    agent = FixerAgent(Settings(), model=FakeModel(fixed))
    update = await agent.run(_state(tmp_path))

    assert update["fixer"]["files_written"] == ["app/api.py"]
    assert update["fixer"]["iterations"] == 1
    assert "addressed" in update["fixer"]["note"]
    # the fix was written into the worktree, committed, and pushed
    assert (tmp_path / "app/api.py").read_text().endswith("raise ValueError")
    assert ws_holder["ws"].pushes == ["agent/fix-42"]
    assert ws_holder["ws"].commits and "fix" in ws_holder["ws"].commits[0]
