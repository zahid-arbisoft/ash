"""Tests for the DevAgent bounded loop (A1 / P2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ash.agents.dev import DevAgent, _build_pr_body, _load_skills, apply_change
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
    update = await DevAgent(Settings()).run(state)
    assert "skipped" in update["dev"]["note"]


async def test_coding_skips_without_work_target(tmp_path, monkeypatch):
    cfg = ProjectConfig(name="plane")  # no work target
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    state = _state_with_worktree(tmp_path)
    update = await DevAgent(Settings()).run(state)
    assert "skipped" in update["dev"]["note"]


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
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_test_command", lambda p: None)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.dev.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.dev._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.dev.RepoWorkspace", lambda w, d, **kw: MagicMock())
    monkeypatch.setattr("ash.agents.dev.create_pr", lambda **kw: "https://gh/pr/9")

    async def fake_ensure(project: object, state: object, **kw: object) -> tuple[Path, str]:
        return tmp_path, "agent/issue-42-add-health"

    monkeypatch.setattr("ash.agents.dev.ensure_worktree", fake_ensure)

    # raw_issue gives a brief; research namespace is empty (no plan / no worktree).
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="add health", body="GET /health → 200")

    captured: dict[str, object] = {}

    async def fake_code(worktree: Path, brief: str, plan: object, **kwargs: object) -> CodeChange:
        captured["plan"] = plan
        return _change()

    agent = DevAgent(Settings())
    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert update["dev"]["pr_url"] == "https://gh/pr/9"
    assert update["dev"]["worktree_path"] == str(tmp_path)
    assert update["dev"]["branch"] == "agent/issue-42-add-health"
    assert captured["plan"] is None  # built from the brief alone, no research plan


# ── full run with mocks ───────────────────────────────────────────────────────


async def test_coding_full_run_green_tests(tmp_path, monkeypatch):
    """Happy path: _code returns edits, tests pass on first iteration → PR opened."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_test_command", lambda p: "pytest")
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.dev.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.dev._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.dev._run_in_worktree", lambda cmd, wt: (0, "1 passed"))

    ws_mock = MagicMock()
    monkeypatch.setattr("ash.agents.dev.RepoWorkspace", lambda w, d, **kw: ws_mock)
    monkeypatch.setattr("ash.agents.dev.create_pr", lambda **kw: "https://gh/pr/1")

    state = _state_with_worktree(tmp_path)
    agent = DevAgent(Settings())
    # Patch _code directly so we don't need a live LLM or create_agent
    agent._code = AsyncMock(return_value=_change())  # type: ignore[method-assign]
    update = await agent.run(state)

    assert update["dev"]["pr_url"] == "https://gh/pr/1"
    assert "passed" in update["dev"]["note"]
    assert ws_mock.commit_all.called
    assert ws_mock.push_branch.called


async def test_coding_retries_on_test_failure(tmp_path, monkeypatch):
    """If tests fail on iteration 1, _code is called again with failure context."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_test_command", lambda p: "pytest")
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.dev.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.dev._load_skills", lambda p: "")

    test_call_count = {"n": 0}

    def mock_run(cmd: str, wt: Path) -> tuple[int, str]:
        test_call_count["n"] += 1
        return (1, "FAILED test_api") if test_call_count["n"] == 1 else (0, "1 passed")

    monkeypatch.setattr("ash.agents.dev._run_in_worktree", mock_run)
    monkeypatch.setattr("ash.agents.dev.RepoWorkspace", lambda w, d, **kw: MagicMock())
    monkeypatch.setattr("ash.agents.dev.create_pr", lambda **kw: "https://gh/pr/2")

    state = _state_with_worktree(tmp_path)
    agent = DevAgent(Settings())
    code_call_count = {"n": 0}

    async def fake_code(*args: object, **kwargs: object) -> CodeChange:
        code_call_count["n"] += 1
        return _change()

    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert test_call_count["n"] == 2   # test ran twice
    assert code_call_count["n"] == 2   # _code called twice (initial + fix)
    assert update["dev"]["pr_url"] == "https://gh/pr/2"


# ── Dev HITL: human feedback ───────────────────────────────────────────────────


async def test_coding_threads_human_feedback_into_code_and_clears_it(tmp_path, monkeypatch):
    """When CodingState.feedback is set, it reaches _code as human_feedback and is cleared after."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_test_command", lambda p: None)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.dev.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.dev._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.dev.RepoWorkspace", lambda w, d, **kw: MagicMock())
    monkeypatch.setattr("ash.agents.dev.create_pr", lambda **kw: "https://gh/pr/7")

    state = _state_with_worktree(tmp_path)
    state.dev.feedback = "use the existing AuthService instead of a new client"

    captured: dict[str, object] = {}

    async def fake_code(worktree, brief, plan, *, human_feedback="", **kwargs):
        captured["human_feedback"] = human_feedback
        return _change()

    agent = DevAgent(Settings())
    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert captured["human_feedback"] == "use the existing AuthService instead of a new client"
    assert update["dev"]["feedback"] is None  # cleared after consumption
    assert update["dev"]["pr_url"] == "https://gh/pr/7"


async def test_code_prompt_includes_human_feedback(tmp_path, monkeypatch):
    """The feedback block is injected into the coding user prompt so the model must address it."""
    captured: dict[str, str] = {}

    class FakeToolkit:
        def __init__(self, **kw):
            pass

        def get_tools(self):
            return []

    async def fake_generate(schema, *, system, user, tools=None, **kw):
        captured["user"] = user
        return _change()

    monkeypatch.setattr("ash.agents.dev.DevToolkit", FakeToolkit)
    agent = DevAgent(Settings())
    agent.generate = fake_generate  # type: ignore[method-assign]

    await agent._code(
        tmp_path,
        "brief",
        _plan(),
        skills_context="",
        test_cmd=None,
        test_failure=None,
        is_fix_pass=False,
        human_feedback="handle the empty-list case",
    )
    assert "Human feedback you MUST address" in captured["user"]
    assert "handle the empty-list case" in captured["user"]


async def test_code_prompt_includes_custom_prompt_and_records_context(tmp_path, monkeypatch):
    """A cockpit custom prompt is injected into the user prompt; the brief is recorded as
    `context` and the plan as `code` for the I/O page (decision #33)."""
    captured: dict[str, object] = {}

    class FakeToolkit:
        def __init__(self, **kw):
            pass

        def get_tools(self):
            return []

    async def fake_generate(schema, *, system, user, tools=None, context=None, code=None, **kw):
        captured["user"] = user
        captured["context"] = context
        captured["code"] = code
        return _change()

    monkeypatch.setattr("ash.agents.dev.DevToolkit", FakeToolkit)
    agent = DevAgent(Settings())
    agent.generate = fake_generate  # type: ignore[method-assign]

    await agent._code(
        tmp_path,
        "the work brief",
        _plan(),
        skills_context="",
        test_cmd=None,
        test_failure=None,
        is_fix_pass=False,
        custom_prompt="prefer async I/O",
    )
    assert "Additional instructions" in captured["user"]
    assert "prefer async I/O" in captured["user"]
    assert captured["context"] == "the work brief"  # brief recorded as context
    assert captured["code"] and "add /health endpoint" in captured["code"]  # plan recorded as code


async def test_dev_threads_run_prompt_via_custom_prompts(tmp_path, monkeypatch):
    """run_prompt (run-wide) reaches the Dev agent through _extra_instructions."""
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_test_command", lambda p: None)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.dev.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.dev._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.dev.RepoWorkspace", lambda w, d, **kw: MagicMock())
    monkeypatch.setattr("ash.agents.dev.create_pr", lambda **kw: "https://gh/pr/5")

    state = _state_with_worktree(tmp_path)
    state.run_prompt = "follow the house style guide"

    seen: dict[str, str] = {}

    async def fake_code(worktree, brief, plan, *, custom_prompt="", **kwargs):
        seen["custom_prompt"] = custom_prompt
        return _change()

    agent = DevAgent(Settings())
    agent._code = fake_code  # type: ignore[method-assign]
    await agent.run(state)

    assert "follow the house style guide" in seen["custom_prompt"]


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


# ── F7: combined-PR strategy ───────────────────────────────────────────────────


def _combined_mocks(tmp_path, monkeypatch):
    cfg = _project_cfg()
    monkeypatch.setattr("ash.agents.dev.load_project", lambda n: cfg)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_test_command", lambda p: None)
    monkeypatch.setattr("ash.agents.dev.code_intel.detect_commit_convention", lambda p: "cc")
    monkeypatch.setattr("ash.agents.dev.code_intel.read_pr_template", lambda p: None)
    monkeypatch.setattr("ash.agents.dev._load_skills", lambda p: "")
    monkeypatch.setattr("ash.agents.dev.RepoWorkspace", lambda w, d, **kw: MagicMock())

    async def fake_ensure(project: object, state: object, **kw: object) -> tuple[Path, str]:
        # Mirrors ensure_worktree's single-strategy seed: one shared run-level branch.
        return tmp_path, "agent/issue-run-r1-combined"

    monkeypatch.setattr("ash.agents.dev.ensure_worktree", fake_ensure)


def _combined_state() -> WorkflowState:
    state = WorkflowState(run_id="r1", project="plane", item_id="42", story_mode="multiple")
    state.pr_strategy = "single"
    state.raw_issue = RawIssue(id="42", title="combined", body="do a few things")
    return state


async def test_combined_pr_first_story_opens_and_records_run_level_identity(tmp_path, monkeypatch):
    """First story under the single-PR strategy opens the PR and records the shared
    branch/worktree/pr_url at RUN level so later stories stack onto it."""
    _combined_mocks(tmp_path, monkeypatch)
    monkeypatch.setattr("ash.agents.dev.create_pr", lambda **kw: "https://gh/pr/combined")

    state = _combined_state()  # no combined_pr_url yet

    async def fake_code(worktree, brief, plan, **kwargs):
        return _change()

    agent = DevAgent(Settings())
    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert update["dev"]["pr_url"] == "https://gh/pr/combined"
    # Run-level shared identity is passed through (the node adapter forwards these top-level keys).
    assert update["combined_pr_url"] == "https://gh/pr/combined"
    assert update["combined_branch"] == "agent/issue-run-r1-combined"
    assert update["combined_worktree"] == str(tmp_path)


async def test_combined_pr_subsequent_story_reuses_existing_pr(tmp_path, monkeypatch):
    """A later story reuses the run-level combined PR (edits it) instead of opening a new one."""
    _combined_mocks(tmp_path, monkeypatch)

    created: list[bool] = []
    edited: list[str] = []
    monkeypatch.setattr(
        "ash.agents.dev.create_pr", lambda **kw: created.append(True) or "https://gh/pr/NEW"
    )
    monkeypatch.setattr(
        "ash.agents.dev.pr_client.edit_pr_body",
        lambda *, pr, body: edited.append(pr),
    )

    state = _combined_state()
    state.combined_pr_url = "https://gh/pr/combined"  # opened by an earlier story

    async def fake_code(worktree, brief, plan, **kwargs):
        return _change()

    agent = DevAgent(Settings())
    agent._code = fake_code  # type: ignore[method-assign]
    update = await agent.run(state)

    assert not created, "must not open a second PR for the shared branch"
    assert edited == ["https://gh/pr/combined"]
    assert update["dev"]["pr_url"] == "https://gh/pr/combined"
    assert update["combined_pr_url"] == "https://gh/pr/combined"


async def test_ensure_worktree_single_strategy_uses_shared_run_branch(tmp_path, monkeypatch):
    """Under the single-PR strategy, ensure_worktree seeds ONE run-level branch (not per-ticket)
    and reuses an existing worktree via open_or_create_worktree."""
    from ash.agents import worktree as wt_mod

    cfg = ProjectConfig(
        name="plane", work=WorkTarget(target_repo="o/r", local_repo_path=str(tmp_path))
    )
    ws = MagicMock()
    ws.sync_base.return_value = "origin/main"
    ws.branch_name_from.return_value = "agent/issue-run-abcd1234-combined"
    ws.open_or_create_worktree.return_value = tmp_path / "wt"
    monkeypatch.setattr("ash.agents.worktree.RepoWorkspace", lambda w, d, **kw: ws)

    state = WorkflowState(run_id="abcd1234 effff", project="plane", item_id="42")
    state.pr_strategy = "single"
    state.ticket_id = "T-2"  # per-ticket id must be IGNORED in single strategy

    result = await wt_mod.ensure_worktree(cfg, state)
    assert result is not None
    _wt, branch = result
    # branch seeded from the run, not the ticket
    seed_arg = ws.branch_name_from.call_args.args[0]
    assert seed_arg.startswith("run-") and "T-2" not in seed_arg
    assert branch == "agent/issue-run-abcd1234-combined"
    ws.open_or_create_worktree.assert_called_once()
    ws.create_worktree.assert_not_called()
