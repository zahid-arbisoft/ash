"""Research / Spike agent (read-only) — grounds a plan in the actual repo, in a git worktree.

Sets up the per-ticket worktree (parallel-safety primitive), indexes the worktree into Chroma for
semantic search, then produces a grounded `ImplementationPlan` via a tool-calling loop. Works from
the PM spec when one exists, or directly from the raw issue (`raw_to_dev`). No writes happen here.
With no local clone configured it records a skip note so a PM-only run still completes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from langchain.agents import create_agent

from ash.agents.base import BaseAgent
from ash.clients import code_intel
from ash.clients.chroma import VectorStoreClient
from ash.clients.git_repo import RepoWorkspace
from ash.config.settings import load_project
from ash.graph.state import WorkflowState
from ash.schemas import ImplementationPlan
from ash.toolkits.codebase import CodebaseToolkit

logger = logging.getLogger(__name__)

_SYSTEM = """You are a senior engineer doing a research spike. Given the work brief and a real \
view of the repository, produce a concrete, grounded implementation plan. Use the search_codebase \
tool to find relevant files and code before writing your plan. Reference ACTUAL files/paths you \
discover. Prefer the smallest change that satisfies the brief. Do not invent files that aren't \
plausible for this codebase. List open questions instead of guessing."""


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
        logger.info("[research] syncing upstream remote")
        await asyncio.to_thread(ws.ensure_upstream)
        logger.info("[research] syncing base branch")
        base_ref = await asyncio.to_thread(ws.sync_base)
        branch = ws.branch_name_from(state.item_id, state.issue_title)
        logger.info("[research] creating worktree branch=%s base=%s", branch, base_ref)
        wt_path = await asyncio.to_thread(ws.create_worktree, branch, base_ref)
        logger.info("[research] worktree ready at %s", wt_path)

        client = VectorStoreClient(
            host=self.settings.chroma_host,
            port=self.settings.chroma_port,
            collection=state.project,
        )
        logger.info("[research] resetting chroma collection=%s", state.project)
        await asyncio.to_thread(client.reset)
        logger.info("[research] indexing worktree into chroma")
        n = await asyncio.to_thread(client.index_directory, wt_path)
        logger.info("[research] indexed %d files", n)

        logger.info("[research] starting llm plan loop")
        plan = await self._plan(wt_path, client, brief)
        n_tickets = len(plan.tickets) if hasattr(plan, "tickets") else -1
        logger.info("[research] plan complete tickets=%d", n_tickets)
        return {
            "research": {
                "plan": plan,
                "branch": branch,
                "worktree_path": str(wt_path),
            }
        }

    async def _plan(
        self, worktree: Path, client: VectorStoreClient, brief: str
    ) -> ImplementationPlan:
        tree = await asyncio.to_thread(code_intel.repo_tree, worktree)
        toolkit = CodebaseToolkit(client=client, root=worktree)
        agent: Any = create_agent(
            model=self.get_model(),
            tools=toolkit.get_tools(),
            system_prompt=_SYSTEM,
            response_format=ImplementationPlan,
        )
        user = (
            f"## Work brief\n{brief}\n\n"
            f"## Repository overview (top levels)\n{tree}\n\n"
            "Use search_codebase to find relevant code, then produce the implementation plan."
        )
        result = await agent.ainvoke({"messages": [("user", user)]})
        return cast(ImplementationPlan, result["structured_response"])
