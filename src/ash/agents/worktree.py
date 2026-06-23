"""Shared per-run git worktree setup.

Both the Research and Coding agents need an isolated worktree+branch off the base branch.
Research normally creates it; but when Research is disabled or skipped, Coding must be able to
set it up itself so the build still proceeds. Keeping this in one place means the two agents
always produce the same deterministic branch name for a given run/ticket.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ash.clients.git_repo import RepoWorkspace
from ash.graph.state import WorkflowState

logger = logging.getLogger(__name__)


async def ensure_worktree(
    project: Any,
    state: WorkflowState,
    *,
    github_token: str = "",
) -> tuple[Path, str] | None:
    """Create (or recreate) the per-run worktree + branch off the synced base branch.

    Returns ``(worktree_path, branch)`` or ``None`` when no local clone is configured
    (``work.local_repo_path`` / ``LOCAL_REPO_PATH``) — callers then skip gracefully.

    The branch is seeded from the ticket id when the run targets a single ticket, else the
    run's item id — so a per-ticket run gets its own branch.

    ``github_token`` is embedded in the push URL for headless HTTPS auth (Docker / CI).
    """
    work = project.work
    local = work.resolved_local_path() if work else None
    if work is None or local is None or not local.exists():
        return None

    ws = RepoWorkspace(work, project.runtime_dir / "worktrees", github_token=github_token)
    logger.info("[worktree] syncing upstream + base for run=%s", state.run_id)
    await asyncio.to_thread(ws.ensure_upstream)
    base_ref = await asyncio.to_thread(ws.sync_base)

    # Combined-PR strategy (F7): all stories share ONE run-level branch/worktree and stack commits
    # into a single PR. The first story creates it; later stories REUSE it (so the second story
    # builds on top of the first instead of wiping it). Per-story strategy (default) keeps the
    # per-ticket branch + a fresh worktree from base, exactly as before.
    if state.pr_strategy == "single":
        branch = state.combined_branch or ws.branch_name_from(
            f"run-{state.run_id[:8]}", state.issue_title or "combined"
        )
        wt_path = await asyncio.to_thread(ws.open_or_create_worktree, branch, base_ref)
        logger.info("[worktree] single-PR shared worktree branch=%s at %s", branch, wt_path)
        return wt_path, branch

    branch_seed = state.ticket_id or state.item_id
    branch = ws.branch_name_from(branch_seed, state.issue_title)
    logger.info("[worktree] creating worktree branch=%s base=%s", branch, base_ref)
    wt_path = await asyncio.to_thread(ws.create_worktree, branch, base_ref)
    logger.info("[worktree] ready at %s", wt_path)
    return wt_path, branch
