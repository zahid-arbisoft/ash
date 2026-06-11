"""Research / Spike agent (read-only) — grounds a plan in the actual repo, in a git worktree.

Sets up the per-ticket worktree (parallel-safety primitive), orients the model with a shallow repo
tree + ripgrep hits, and produces a grounded `ImplementationPlan`. No writes happen here. If the
project has no local clone configured, it records a skip note so a PM-only run still completes.
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
from ash.schemas import ImplementationPlan, Spec

_SYSTEM = """You are a senior engineer doing a research spike. Given a spec and a real view of the \
repository, produce a concrete, grounded implementation plan. Reference ACTUAL files/paths you see \
in the provided repo overview and search hits. Prefer the smallest change that satisfies the spec. \
Do not invent files that aren't plausible for this codebase. List open questions instead of \
guessing."""

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
        spec = state.pm.spec
        if spec is None:
            return {"research": {"note": "skipped: no spec from PM"}}

        project = load_project(state.project)
        if project.work.resolved_local_path() is None:
            return {
                "research": {
                    "note": "skipped: no local clone configured "
                    "(set work.local_repo_path or LOCAL_REPO_PATH)"
                }
            }

        ws = RepoWorkspace(project.work, project.runtime_dir / "worktrees")
        await asyncio.to_thread(ws.ensure_upstream)
        base_ref = await asyncio.to_thread(ws.sync_base)
        branch = ws.branch_name(int(state.item_id), state.issue_title)
        wt_path = await asyncio.to_thread(ws.create_worktree, branch, base_ref)

        plan = await self._plan(wt_path, state.issue_title, spec)
        return {
            "research": {
                "plan": plan,
                "branch": branch,
                "worktree_path": str(wt_path),
            }
        }

    async def _plan(self, worktree: Path, issue_title: str, spec: Spec) -> ImplementationPlan:
        tree = await asyncio.to_thread(code_intel.repo_tree, worktree)
        hits: list[str] = []
        for kw in _keywords(spec, issue_title):
            hits += await asyncio.to_thread(code_intel.search, worktree, kw, 8)
        hits = hits[:40]

        user = (
            f"## Spec\n{spec.model_dump_json(indent=2)}\n\n"
            f"## Repository overview (top levels)\n{tree}\n\n"
            "## Search hits for issue keywords\n" + ("\n".join(hits) or "(none)") + "\n\n"
            "Produce the implementation plan."
        )
        return await self.generate(ImplementationPlan, system=_SYSTEM, user=user)


def _keywords(spec: Spec, issue_title: str) -> list[str]:
    text = f"{issue_title} {spec.epic.title} {spec.epic.summary}"
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text.lower())
    seen: list[str] = []
    for w in words:
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:6]
