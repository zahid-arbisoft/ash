"""Dev / Coding agent (write) — turns the plan into real file edits, commits, pushes, opens a PR.

Architecture (A1 / P2 — bounded create_agent tool loop):
- Detects the repo's test command, commit convention, PR template, and project skills before coding.
- Inner loop: `create_agent` uses DevToolkit tools (read_file, list_files, search_code, run_command)
  to understand the codebase and verify its own changes, then returns a `CodeChange`.
- Outer loop: `apply_change` writes the edits, we run the test suite ourselves, and if tests fail
  we re-enter `create_agent` with the failure output (up to MAX_CODE_ITERATIONS).
- `apply_change` is public — the Fixer agent reuses it on the same worktree/branch.
- The worktree is left in place for Reviewer/Fixer; the `merge` node removes it.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from ash.agents.base import BaseAgent
from ash.agents.worktree import ensure_worktree
from ash.clients import code_intel
from ash.clients import pr as pr_client
from ash.clients.git_repo import RepoWorkspace
from ash.clients.pr import create_pr
from ash.config.settings import REPO_ROOT, ProjectConfig, load_project
from ash.gates import ApprovalGate
from ash.graph.state import WorkflowState
from ash.schemas import CodeChange, EditAction, ImplementationPlan
from ash.toolkits.dev import DevToolkit

logger = logging.getLogger(__name__)

MAX_CODE_ITERATIONS = 3  # outer test-fix loop ceiling

_SYSTEM = """You are a senior engineer implementing a planned change \
inside an isolated git worktree.

Your job:
1. Use `read_file`, `list_files`, and `search_code` to understand the current codebase state.
2. Implement the minimal, focused change described in the brief and plan.
3. Use `run_command` to run tests/lint and verify your work before finalising.
4. Return a `CodeChange` with the FULL new content for every file you create or modify.

Rules:
- Return FULL file contents — never diffs or patches.
- Only touch files necessary for the task. Match the surrounding code style exactly.
- If a previous test run failed (shown below), fix those specific errors first.
- Keep changes small; prefer modifying listed files over inventing new ones.
- Run tests before returning; if they pass, your work is done."""

_SYSTEM_FIX = """You are a senior engineer fixing test failures in an isolated git worktree.

A previous implementation attempt failed the test suite. Your job:
1. Read the failing test output below carefully.
2. Use `read_file` to inspect the current file contents (they reflect the previous attempt).
3. Fix ONLY what is needed to make the tests pass — do not refactor unrelated code.
4. Use `run_command` to verify the fix before returning.
5. Return a `CodeChange` with the FULL corrected content for each file you change."""


class CodingAgent(BaseAgent):
    name = "coding"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        self._reset_usage()
        skip = await self._trigger_gate(state)
        if skip is not None:
            return skip

        brief = state.brief(max_chars=self.settings.brief_max_chars)
        if not brief:
            return {"coding": {"note": "skipped: nothing to build (no spec or issue)"}}

        project = load_project(state.project)
        if project.work is None:
            return {"coding": {"note": "skipped: project has no work target"}}
        work = project.work

        # Research is optional. If it ran, reuse its plan + worktree; if it was disabled or
        # skipped, work straight from the brief and set up the worktree ourselves.
        plan = state.research.plan  # may be None
        wt = state.research.worktree_path
        branch = state.research.branch
        if wt is not None and branch is not None:
            wt_path = Path(wt)
        else:
            setup = await ensure_worktree(project, state, github_token=self.settings.github_token)
            if setup is None:
                return {
                    "coding": {
                        "note": "skipped: no local clone available "
                        "(configure work.local_repo_path / LOCAL_REPO_PATH for this project)"
                    }
                }
            wt_path, branch = setup
            logger.info("[coding] research absent — set up own worktree at %s", wt_path)
        ws = RepoWorkspace(work, project.runtime_dir / "worktrees",
                           github_token=self.settings.github_token)

        # ── Detect repo conventions & load context ──────────────────────────
        test_cmd = await asyncio.to_thread(code_intel.detect_test_command, wt_path)
        commit_convention = await asyncio.to_thread(code_intel.detect_commit_convention, wt_path)
        pr_template = await asyncio.to_thread(code_intel.read_pr_template, wt_path)
        skills_context = _load_skills(project)
        logger.info(
            "[coding] context: test_cmd=%s convention=%s skills=%s",
            test_cmd,
            commit_convention[:40],
            bool(skills_context),
        )

        # ── Outer test-fix loop ─────────────────────────────────────────────
        test_failure: str | None = None
        change: CodeChange | None = None
        written: list[str] = []

        for iteration in range(MAX_CODE_ITERATIONS):
            logger.info("[coding] iteration %d/%d", iteration + 1, MAX_CODE_ITERATIONS)
            is_fix = test_failure is not None
            change = await self._code(
                wt_path,
                brief,
                plan,
                skills_context=skills_context,
                test_cmd=test_cmd,
                test_failure=test_failure,
                is_fix_pass=is_fix,
            )

            if not change.edits:
                logger.info("[coding] no edits produced on iteration %d", iteration + 1)
                break

            written = await asyncio.to_thread(apply_change, wt_path, change)
            logger.info("[coding] wrote %d files: %s", len(written), written)

            if not test_cmd:
                logger.info("[coding] no test command detected — skipping test gate")
                break

            exit_code, test_out = await asyncio.to_thread(
                _run_in_worktree, test_cmd, wt_path
            )
            logger.info("[coding] tests exit=%d", exit_code)
            if exit_code == 0:
                logger.info("[coding] tests green — done")
                test_failure = None
                break
            if iteration + 1 < MAX_CODE_ITERATIONS:
                logger.warning("[coding] tests failed; will attempt fix (iter %d)", iteration + 2)
                test_failure = test_out
            else:
                logger.warning(
                    "[coding] tests still failing after %d iterations", MAX_CODE_ITERATIONS
                )
                # Proceed anyway — Reviewer will flag it
                test_failure = test_out

        if not written or change is None or not change.edits:
            return {
                "coding": {
                    "change": change,
                    "worktree_path": str(wt_path),
                    "branch": branch,
                    "note": "no edits produced; needs human",
                }
            }

        # ── Commit, push, open PR ───────────────────────────────────────────
        prefix = f"#{state.item_id} " if state.item_id and state.item_id != "upload" else ""
        msg = f"{prefix}implement: {state.issue_title or state.item_id}"
        await asyncio.to_thread(ws.commit_all, wt_path, msg)
        # force: this agent branch is bot-owned; on a re-run we replace the stale prior attempt.
        await asyncio.to_thread(ws.push_branch, wt_path, branch, force=True)

        body = _build_pr_body(
            state=state,
            change=change,
            written=written,
            test_cmd=test_cmd,
            test_failure=test_failure,
            pr_template=pr_template,
        )
        # No-duplicate-PR (decision #26 / F2): if this story already has a PR (a regenerate /
        # retry), update it in place instead of opening a new one. The branch is deterministic
        # per ticket, so even a fresh create_pr would de-dupe — but reusing the known URL avoids
        # a redundant POST and keeps the same PR across regenerations.
        existing_pr = state.coding.pr_url
        title_prefix = f"#{state.item_id}: " if prefix else ""
        if existing_pr:
            await asyncio.to_thread(pr_client.edit_pr_body, pr=existing_pr, body=body)
            pr_url = existing_pr
            logger.info("[coding] updated existing PR for story (no duplicate): %s", pr_url)
        else:
            pr_url = await asyncio.to_thread(
                create_pr,
                target_repo=work.target_repo,
                base=work.base_branch,
                head=branch,
                title=f"[agent] {title_prefix}{state.issue_title or 'implement change'}",
                body=body,
                draft=True,
            )

        gate = ApprovalGate(project.autonomy)
        tests_status = (
            "failing"
            if test_failure
            else ("passed" if test_cmd else "not run — no test command detected")
        )
        note = (
            "awaiting human review/merge"
            if gate.requires_human("merge")
            else "PR open; Reviewer will assess for auto-merge"
        )
        return {
            "coding": {
                "change": change,
                "files_written": written,
                "pr_url": pr_url,
                "worktree_path": str(wt_path),
                "branch": branch,
                "note": f"{note} | tests: {tests_status}",
                "tokens": dict(self._usage),
            }
        }

    async def _code(
        self,
        worktree: Path,
        brief: str,
        plan: ImplementationPlan | None,
        *,
        skills_context: str,
        test_cmd: str | None,
        test_failure: str | None,
        is_fix_pass: bool,
    ) -> CodeChange:
        toolkit = DevToolkit(worktree=worktree, allowed_cmd=test_cmd)
        skills_section = (
            f"\n\n## Project skills / guidelines\n{skills_context}" if skills_context else ""
        )
        test_section = f"\n\nAvailable test command: `{test_cmd}`" if test_cmd else ""
        system = _SYSTEM_FIX if is_fix_pass else _SYSTEM
        failure_section = (
            "\n\n## Test failure to fix\n```\n" + test_failure[:3000] + "\n```"
            if is_fix_pass and test_failure
            else ""
        )
        # The plan is optional: present when Research ran, absent when it's disabled/skipped —
        # in which case the agent plans as it goes using the exploration tools.
        plan_section = (
            f"\n\n## Implementation plan\n{plan.model_dump_json(indent=2)}"
            if plan is not None
            else "\n\nNo research plan was produced — explore the codebase and decide the "
            "minimal change yourself."
        )
        # No pre-dumped tree or file contents — the agent uses read_file / list_files / search_code
        # tools to explore exactly what it needs, keeping the initial prompt lean.
        user = (
            f"## Work brief\n{brief}"
            f"{plan_section}\n\n"
            "Use read_file and list_files to inspect the current code before making changes."
            f"{failure_section}"
            f"{skills_section}"
            f"{test_section}\n\n"
            "Use the tools to read files, run tests to verify, "
            "then return the CodeChange with full file contents."
        )
        return await self.generate(CodeChange, system=system, user=user, tools=toolkit.get_tools())


# ── Helpers ───────────────────────────────────────────────────────────────────


def apply_change(worktree: Path, change: CodeChange) -> list[str]:
    """Write edits into the worktree (sandboxed). Returns the written paths.

    Public — shared by the Coding and Fixer agents.
    """
    written: list[str] = []
    root = worktree.resolve()
    for edit in change.edits:
        target = (worktree / edit.path).resolve()
        if not str(target).startswith(str(root)):
            raise ValueError(f"edit path escapes worktree: {edit.path}")
        if edit.action == EditAction.modify and not target.exists():
            pass  # treat as create if the model mislabelled
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.content)
        written.append(edit.path)
    return written


def _run_in_worktree(cmd: str, worktree: Path) -> tuple[int, str]:
    """Run a shell command in the worktree; return (exit_code, output)."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,  # noqa: S602
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(worktree),
        )
        out = (proc.stdout + proc.stderr)[:4000]
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return 1, "timeout: command exceeded 120s"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _load_skills(project: ProjectConfig) -> str:
    """Load the project's SKILL.md if configured."""
    if not project.skills:
        return ""
    skill_path = REPO_ROOT / "skills" / project.skills / "SKILL.md"
    if skill_path.is_file():
        return skill_path.read_text(errors="ignore")[:4000]
    return ""


def _build_pr_body(
    *,
    state: WorkflowState,
    change: CodeChange,
    written: list[str],
    test_cmd: str | None,
    test_failure: str | None,
    pr_template: str | None,
) -> str:
    """Build the PR description; fills a template if the repo has one."""
    files_md = ", ".join(f"`{f}`" for f in written)
    tests_note = (
        f"✅ `{test_cmd}` passed"
        if test_cmd and not test_failure
        else (
            f"⚠ `{test_cmd}` failed after {MAX_CODE_ITERATIONS} attempts — see note"
            if test_failure
            else "n/a"
        )
    )
    board_ref = getattr(state.pm, "board_ref", None) or ""

    if pr_template:
        # Populate common template placeholders; leave the rest intact.
        body = pr_template
        for placeholder, value in (
            ("## Description", f"## Description\n{change.summary}"),
            ("## Summary", f"## Summary\n{change.summary}"),
        ):
            if placeholder in body:
                body = body.replace(placeholder, value, 1)
                break
        else:
            body = f"{change.summary}\n\n{body}"
        return (
            body
            + f"\n\n---\n**Files:** {files_md}  \n**Tests:** {tests_note}  \n"
            + (f"**Spec:** `{board_ref}`  \n" if board_ref else "")
            + "_Generated by the ASH build team._"
        )

    item_ref = f"{state.item_id} — {state.issue_url}" if state.issue_url else state.item_id
    return (
        f"Implements {item_ref}\n\n"
        f"{change.summary}\n\n"
        f"**Files changed:** {files_md}\n\n"
        f"**Tests:** {tests_note}\n\n"
        f"**Tests note:** {change.tests_note or 'n/a'}\n\n"
        + (f"**Spec on board:** `{board_ref}`\n\n" if board_ref else "")
        + "_Generated by the ASH build team._"
    )
