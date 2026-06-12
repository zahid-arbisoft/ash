"""Research / Spike agent (read-only) — grounds a plan in the actual repo, in a git worktree.

Sets up the per-ticket worktree (parallel-safety primitive), orients the model with a shallow repo
tree + ripgrep hits, and produces a grounded `ImplementationPlan`. Works from the PM spec when one
exists, or directly from the raw issue (`raw_to_dev`). No writes happen here. With no local clone
configured it records a skip note so a PM-only run still completes.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from ash.agents.base import BaseAgent
from ash.clients import code_intel
from ash.clients.git_repo import RepoWorkspace
from ash.config.settings import load_project
from ash.graph.state import WorkflowState
from ash.schemas import ImplementationPlan

_SYSTEM = """You are a senior engineer doing a research spike. Given the work brief and a real \
view of the repository, produce a concrete, grounded implementation plan. Reference ACTUAL \
files/paths you see in the provided repo overview and search hits. Prefer the smallest change \
that satisfies the brief. Do not invent files that aren't plausible for this codebase. List open \
questions instead of guessing."""

_STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "view",
    "list",
    "show",
    "add",
    "feature",
    "bug",
}


class ResearchAgent(BaseAgent):
    name = "research"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        brief = state.brief()
        if not brief:
            return {"research": {"note": "skipped: no spec or raw issue to work from"}}

        project = load_project(state.project)
        work = project.work
        local = work.resolved_local_path() if work else None
        if work is None or local is None or not local.exists():
            return {
                "research": {
                    "note": "skipped: no local clone available "
                    "(configure work.local_repo_path / LOCAL_REPO_PATH for this project)"
                }
            }

        ws = RepoWorkspace(work, project.runtime_dir / "worktrees")
        await asyncio.to_thread(ws.ensure_upstream)
        base_ref = await asyncio.to_thread(ws.sync_base)
        branch = ws.branch_name_from(state.item_id, state.issue_title)
        wt_path = await asyncio.to_thread(ws.create_worktree, branch, base_ref)

        plan = await self._plan(wt_path, state, brief)
        return {
            "research": {
                "plan": plan,
                "branch": branch,
                "worktree_path": str(wt_path),
            }
        }

    async def _plan(self, worktree: Path, state: WorkflowState, brief: str) -> ImplementationPlan:
        tree = await asyncio.to_thread(code_intel.repo_tree, worktree)
        hits: list[str] = []
        for kw in _keywords(state):
            hits += await asyncio.to_thread(code_intel.search, worktree, kw, 8)
        hits = hits[:40]

        user = (
            f"## Work brief\n{brief}\n\n"
            f"## Repository overview (top levels)\n{tree}\n\n"
            "## Search hits for keywords\n" + ("\n".join(hits) or "(none)") + "\n\n"
            "Produce the implementation plan."
        )
        return await self.generate(ImplementationPlan, system=_SYSTEM, user=user)


def _keywords(state: WorkflowState) -> list[str]:
    text = state.issue_title
    if state.pm.spec is not None:
        text = f"{text} {state.pm.spec.epic.title} {state.pm.spec.epic.summary}"
    elif state.raw_issue is not None:
        text = f"{text} {state.raw_issue.body}"
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text.lower())
    seen: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:6]
