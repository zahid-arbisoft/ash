"""Tests for the CodingAgent bounded loop (A1 / P2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ash.agents.coding import CodingAgent, _build_pr_body, _load_skills, apply_change
from ash.config.settings import ProjectConfig, Settings, WorkTarget
from ash.graph.state import ResearchState, WorkflowState
from ash.integrations.base import RawIssue
from ash.schemas import CodeChange, EditAction, FileEdit, ImplementationPlan

# ── helpers ───────────────────────────────────────────────────────────────────


def _plan() -> ImplementationPlan:
    return ImplementationPlan(
        summary="add /health endpoint",
        relevant_files=["src/api.py"],
        new_files=[],
        steps=["Add GET /health returning 200"],
    )


def _change(path: str = "src/api.py") -> CodeChange:
    return CodeChange(
        summary="add health endpoint",
        edits=[FileEdit(path=path, action=EditAction.modify, content="# health\n")],
        tests_note="covered",
    )


def _state_with_worktree(wt: Path) -> WorkflowState:
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="add health endpoint", body="GET /health → 200")
    state.research = ResearchState(
        plan=_plan(),
        branch="agent/issue-42-add-health",
        worktree_path=str(wt),
    )
    return state


def _project_cfg() -> ProjectConfig:
    return ProjectConfig(name="plane", work=WorkTarget(target_repo="o/r"))


# ── skip cases ────────────────────────────────────────────────────────────────


async def test_coding_skips_without_plan():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    # research state has no plan
    update = await CodingAgent(Settings()).run(state)
    assert "skipped" in update["coding"]["note"]


async def test_coding_skips_without_work_target(tmp_path, monkeypatch):
    cfg = ProjectConfig(name="plane")  # no work target
    monkeypatch.setattr("ash.agents.coding.load_project", lambda n: cfg)
    state = _state_with_worktree(tmp_path)
    update = await CodingAgent(Settings()).run(state)
    assert "skipped" in update["coding"]["note"]


# ── apply_change ─────────────────────────────────────────────────────────────


def test_apply_change_writes_files(tmp_path):
    change = _change("src/api.py")
    written = apply_change(tmp_path, change)
    assert written == ["src/api.py"]
    assert (tmp_path / "src" / "api.py").read_text() == "# health\n"


def test_apply_change_rejects_path_traversal(tmp_path):
    change = CodeChange(
        summary="evil",
        edits=[FileEdit(path="../etc/passwd", action=EditAction.modify, content="x")],
    )
    with pytest.raises(ValueError, match="escapes worktree"):
        apply_change(tmp_path, change)


# ── coding without research ────────────────────────────────────────────────────


async def test_coding_works_without_research(tmp_path, monkeypatch):
    """Research disabled → no plan/worktree in state → Coding sets up its own worktree and
    builds straight from the brief (plan is None)."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.coding.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.coding.code_intel.detect_test_command", lambda p: None)
    monkeypatch.setattr("ash.agents.coding.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.coding.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.coding._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.coding.RepoWorkspace", lambda w, d, **kw: MagicMock())
    monkeypatch.setattr("ash.agents.coding.create_pr", lambda **kw: "https://gh/pr/9")

    async def fake_ensure(project: object, state: object, **kw: object) -> tuple[Path, str]:
        return tmp_path, "agent/issue-42-add-health"

    monkeypatch.setattr("ash.agents.coding.ensure_worktree", fake_ensure)

    # raw_issue gives a brief; research namespace is empty (no plan / no worktree).
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="add health", body="GET /health → 200")

    captured: dict[str, object] = {}

    async def fake_code(worktree: Path, brief: str, plan: object, **kwargs: object) -> CodeChange:
        captured["plan"] = plan
        return _change()

    agent = CodingAgent(Settings())
    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert update["coding"]["pr_url"] == "https://gh/pr/9"
    assert update["coding"]["worktree_path"] == str(tmp_path)
    assert update["coding"]["branch"] == "agent/issue-42-add-health"
    assert captured["plan"] is None  # built from the brief alone, no research plan


# ── full run with mocks ───────────────────────────────────────────────────────


async def test_coding_full_run_green_tests(tmp_path, monkeypatch):
    """Happy path: _code returns edits, tests pass on first iteration → PR opened."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.coding.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.coding.code_intel.detect_test_command", lambda p: "pytest")
    monkeypatch.setattr("ash.agents.coding.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.coding.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.coding._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.coding._run_in_worktree", lambda cmd, wt: (0, "1 passed"))

    ws_mock = MagicMock()
    monkeypatch.setattr("ash.agents.coding.RepoWorkspace", lambda w, d, **kw: ws_mock)
    monkeypatch.setattr("ash.agents.coding.create_pr", lambda **kw: "https://gh/pr/1")

    state = _state_with_worktree(tmp_path)
    agent = CodingAgent(Settings())
    # Patch _code directly so we don't need a live LLM or create_agent
    agent._code = AsyncMock(return_value=_change())  # type: ignore[method-assign]
    update = await agent.run(state)

    assert update["coding"]["pr_url"] == "https://gh/pr/1"
    assert "passed" in update["coding"]["note"]
    assert ws_mock.commit_all.called
    assert ws_mock.push_branch.called


async def test_coding_retries_on_test_failure(tmp_path, monkeypatch):
    """If tests fail on iteration 1, _code is called again with failure context."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.coding.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.coding.code_intel.detect_test_command", lambda p: "pytest")
    monkeypatch.setattr("ash.agents.coding.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.coding.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.coding._load_skills", lambda p: "")

    test_call_count = {"n": 0}

    def mock_run(cmd: str, wt: Path) -> tuple[int, str]:
        test_call_count["n"] += 1
        return (1, "FAILED test_api") if test_call_count["n"] == 1 else (0, "1 passed")

    monkeypatch.setattr("ash.agents.coding._run_in_worktree", mock_run)
    monkeypatch.setattr("ash.agents.coding.RepoWorkspace", lambda w, d, **kw: MagicMock())
    monkeypatch.setattr("ash.agents.coding.create_pr", lambda **kw: "https://gh/pr/2")

    state = _state_with_worktree(tmp_path)
    agent = CodingAgent(Settings())
    code_call_count = {"n": 0}

    async def fake_code(*args: object, **kwargs: object) -> CodeChange:
        code_call_count["n"] += 1
        return _change()

    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert test_call_count["n"] == 2   # test ran twice
    assert code_call_count["n"] == 2   # _code called twice (initial + fix)
    assert update["coding"]["pr_url"] == "https://gh/pr/2"


# ── _build_pr_body ────────────────────────────────────────────────────────────


def test_pr_body_uses_template_when_present():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    body = _build_pr_body(
        state=state,
        change=_change(),
        written=["src/api.py"],
        test_cmd="pytest",
        test_failure=None,
        pr_template="## Description\n\n## Tests\n",
    )
    assert "add health endpoint" in body
    assert "src/api.py" in body


def test_pr_body_default_when_no_template():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    body = _build_pr_body(
        state=state,
        change=_change(),
        written=["src/api.py"],
        test_cmd=None,
        test_failure=None,
        pr_template=None,
    )
    assert "add health endpoint" in body
    assert "ASH build team" in body


# ── _load_skills ─────────────────────────────────────────────────────────────


def test_load_skills_returns_empty_when_not_configured():
    cfg = ProjectConfig(name="plane")
    assert _load_skills(cfg) == ""
