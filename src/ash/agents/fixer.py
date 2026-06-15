"""Fixer agent — the Dev agent in "fix mode" (plan §10.5).

When the Reviewer requests changes, the Fixer addresses the blocking findings on the SAME branch and
worktree the Coding agent used, commits, pushes (updating the PR), and refreshes the PR description.
Bounded by `MAX_FIX_ITERATIONS` so the review↔fix loop can never run unbounded.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ash.agents.base import BaseAgent
from ash.agents.coding import apply_change
from ash.clients import pr as pr_client
from ash.clients.git_repo import RepoWorkspace
from ash.config.settings import load_project
from ash.graph.state import WorkflowState
from ash.schemas import CodeChange, CodeReview, ReviewVerdict

MAX_FIX_ITERATIONS = 2

_SYSTEM = """You are a senior engineer fixing your own PR in response to a code review. You are \
given the work brief, the current content of the changed files, and the reviewer's findings. \
Produce the \
MINIMAL set of full-file edits that resolves the findings — especially every `critical` and `high` \
finding. Address `medium`/`low`/`nit` items too when it is safe and cheap.

Rules:
- Return the FULL new content for each file you change (never a diff).
- Fix exactly what the review calls out; do not refactor unrelated code or expand scope.
- Keep tests passing and add/adjust tests when a finding is about missing coverage.
- Match the surrounding code style."""


class FixerAgent(BaseAgent):
    name = "fixer"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        self._reset_usage()
        skip = await self._trigger_gate(state)
        if skip is not None:
            return skip

        review = state.reviewer.review
        if review is None or (
            review.verdict is ReviewVerdict.approve and not review.blocking()
        ):
            return {"fixer": {"note": "skipped: review approved, nothing to fix"}}

        change = state.coding.change
        # Prefer Research's worktree; fall back to the one Coding recorded (set when Research
        # was disabled/skipped and Coding created the worktree itself).
        wt = state.research.worktree_path or state.coding.worktree_path
        branch = state.research.branch or state.coding.branch
        if change is None or wt is None or branch is None:
            return {"fixer": {"note": "skipped: no worktree/change available to fix"}}

        project = load_project(state.project)
        if project.work is None:
            return {"fixer": {"note": "skipped: project has no work target"}}
        work = project.work
        wt_path = Path(wt)
        ws = RepoWorkspace(work, project.runtime_dir / "worktrees",
                           github_token=self.settings.github_token)

        fix = await self._fix(state.brief(max_chars=self.settings.brief_max_chars), change, review)
        if not fix.edits:
            return {"fixer": {"change": fix, "note": "no fix edits produced; needs human"}}

        written = await asyncio.to_thread(apply_change, wt_path, fix)
        prefix = f"#{state.item_id} " if state.item_id and state.item_id != "upload" else ""
        await asyncio.to_thread(
            ws.commit_all, wt_path, f"{prefix}fix: address review feedback"
        )
        await asyncio.to_thread(ws.push_branch, wt_path, branch, force=True)

        pr_url = state.coding.pr_url
        if pr_url:
            await self._refresh_pr_body(pr_url, state, fix, review)

        addressed = len(review.blocking()) or len(review.findings)
        return {
            "fixer": {
                "change": fix,
                "files_written": written,
                "iterations": 1,  # one deterministic pass; bound is MAX_FIX_ITERATIONS
                "pr_url": pr_url,
                "note": f"addressed {addressed} finding(s); PR updated",
                "tokens": dict(self._usage),
            }
        }

    async def _fix(self, brief: str, change: CodeChange, review: CodeReview) -> CodeChange:
        cap = self.settings.files_max_chars
        # Per-file cap = total budget split across the edits, so one huge file can't crowd out
        # the others. Bounds the prompt for small-context models (was previously uncapped).
        per_file = max(1, cap // len(change.edits)) if change.edits else cap
        parts = []
        for e in change.edits:
            body = e.content or ""
            excerpt = body[:per_file] + ("\n… (truncated)" if len(body) > per_file else "")
            parts.append(f"### {e.path}\n```\n{excerpt}\n```")
        current = "\n\n".join(parts)
        findings = "\n".join(
            f"- [{f.severity.value}] {f.path}"
            + (f":{f.line}" if f.line else "")
            + f" — {f.comment}"
            + (f" (suggestion: {f.suggestion})" if f.suggestion else "")
            for f in review.findings
        )
        user = (
            f"## Work brief / spec\n{brief}\n\n"
            f"## Current changed files\n{current}\n\n"
            f"## Reviewer findings to address\n{findings}\n\n"
            "Produce the corrected full file contents that resolve these findings."
        )
        return await self.generate(CodeChange, system=_SYSTEM, user=user)

    async def _refresh_pr_body(
        self, pr_url: str, state: WorkflowState, fix: CodeChange, review: CodeReview
    ) -> None:
        body = (
            f"Implements item {state.item_id} — {state.issue_url}\n\n"
            f"{state.coding.change.summary if state.coding.change else ''}\n\n"
            f"**Review fixes:** {fix.summary}\n\n"
            f"**Addressed:** {len(review.findings)} finding(s) from the Reviewer agent.\n\n"
            f"_Updated by the ASH Fixer agent._"
        )
        try:
            await asyncio.to_thread(pr_client.edit_pr_body, pr=pr_url, body=body)
        except Exception:  # noqa: BLE001 — PR body refresh is best-effort
            pass
