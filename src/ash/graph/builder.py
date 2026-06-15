"""Graph builder — Intake → (PM?) → RFC? → plan_stories → [story loop] → Merge (decision #26).

Intake fetches the issue; a **conditional edge** routes by `intake_mode`:
- `raw_to_spec`  → PM converts the issue to a spec; RFC (opt-in) → plan_stories.
- `spec_ready`   → the spec already exists; PM extracts tickets; RFC → plan_stories.
- `raw_to_dev`   → skip PM/RFC; plan_stories synthesizes one story from the raw issue.

`plan_stories` turns the spec (or raw issue) into one or more `StoryState`s (dependency-sorted).
The **story loop** then builds them **one at a time**:

    plan_stories → story_router ──(next story)──► story_build (subgraph) → story_router ...
                        └──────────(no story left)──────────► merge → END

`story_build` is a compiled subgraph (Research → Coding → Reviewer → Fixer → finalize) over the same
`WorkflowState`, with no own checkpointer — the parent's Postgres checkpointer persists everything,
so interrupts/resume bubble through. Each build node is **story-scoped** (see `graph/nodes.py`).

The `merge` terminal node sets `status` from the per-story outcomes and sweeps any leftover
worktrees. RFC is opt-in (self-skips) and runs once per run (never per story).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from ash.graph.nodes import Agent, make_node
from ash.graph.state import StoryState, WorkflowState
from ash.graph.stories import build_stories, next_story

logger = logging.getLogger(__name__)

# The per-story build team, in order. Each is a story-scoped node (decision #26).
STORY_BUILD_ORDER = ("research", "coding", "reviewer", "fixer")


async def _remove_worktree(project: Any, wt: str | None) -> None:
    if not wt:
        return
    from ash.clients.git_repo import RepoWorkspace

    try:
        if project.work is not None:
            ws = RepoWorkspace(project.work, project.runtime_dir / "worktrees")
            await asyncio.to_thread(ws.remove_worktree, Path(wt))
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass


# ── Story planning ──────────────────────────────────────────────────────────


async def _plan_stories(state: WorkflowState) -> dict[str, Any]:
    """Build the per-story map + dependency order, persist StoryRecords, seed per-story tasks."""
    stories, order = build_stories(state)
    await _persist_stories(state, stories, order)
    await _seed_story_tasks(state, order)
    logger.info("[plan_stories] %d stories, order=%s", len(stories), order)
    return {"stories": stories, "story_order": order, "current_story": ""}


async def _persist_stories(
    state: WorkflowState, stories: dict[str, StoryState], order: list[str]
) -> None:
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.stories import upsert_story

        async with get_sessionmaker()() as session:
            for pos, tid in enumerate(order):
                s = stories[tid]
                await upsert_story(
                    session,
                    run_id=state.run_id,
                    ticket_id=tid,
                    project=state.project,
                    title=s.title,
                    status=s.status,
                    branch=s.branch,
                    pr_url=s.pr_url,
                    position=pos,
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.debug("[plan_stories] persist best-effort failed: %s", exc)


async def _seed_story_tasks(state: WorkflowState, order: list[str]) -> None:
    """Create the first build task (research) per story so the dispatcher/UI can track it."""
    try:
        from ash.config.settings import load_project
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import upsert_agent_task

        project = load_project(state.project)
        r_policy = project.agent_policy("research")
        status = "in_progress" if r_policy.trigger == "auto" else "pending"
        title = state.pm.spec.epic.title if state.pm.spec and state.pm.spec.epic else state.item_id
        async with get_sessionmaker()() as session:
            for tid in order:
                await upsert_agent_task(
                    session,
                    agent_name="research",
                    project=state.project,
                    run_id=state.run_id,
                    item_id=state.item_id,
                    ticket_id=tid,
                    title=title,
                    max_retries=r_policy.max_retries,
                    status=status,
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[plan_stories] seed tasks best-effort failed: %s", exc)


# ── Story loop ────────────────────────────────────────────────────────────────


async def _story_router(state: WorkflowState) -> dict[str, Any]:
    """Pick the next pending story whose deps are satisfied; mark it running. Sets current_story
    to '' when nothing is left (→ merge)."""
    tid = next_story(state)
    if tid is None:
        return {"current_story": ""}
    story = state.stories[tid].model_copy(deep=True)
    story.status = "running"
    logger.info("[story_router] building story %s (%s)", tid, story.title)
    return {"current_story": tid, "stories": {tid: story}}


def _route_story(state: WorkflowState) -> str:
    return "build" if state.current_story else "done"


async def _story_finalize(state: WorkflowState) -> dict[str, Any]:
    """Close out the current story: set terminal status + clean up its worktree."""
    tid = state.current_story
    base = state.stories.get(tid)
    if base is None:
        return {}
    story = base.model_copy(deep=True)
    if story.has_error():
        story.status = "failed"
    else:
        story.status = "completed"
    wt = story.research.worktree_path or story.coding.worktree_path
    from ash.config.settings import load_project

    try:
        project = load_project(state.project)
        await _remove_worktree(project, wt)
    except Exception:  # noqa: BLE001
        pass
    await _persist_story_status(state, story)
    logger.info("[story_finalize] story %s → %s", tid, story.status)
    return {"stories": {tid: story}}


async def _persist_story_status(state: WorkflowState, story: StoryState) -> None:
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.stories import upsert_story

        async with get_sessionmaker()() as session:
            await upsert_story(
                session,
                run_id=state.run_id,
                ticket_id=story.ticket_id,
                project=state.project,
                status=story.status,
                branch=story.branch,
                pr_url=story.pr_url,
                failed_step=story.failed_step,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[story_finalize] persist best-effort failed: %s", exc)


# ── Terminal ──────────────────────────────────────────────────────────────────


async def _merge(state: WorkflowState) -> dict[str, Any]:
    run_errored = any(
        sub.error is not None for sub in (state.intake, state.pm, state.rfc)
    )
    stories_errored = any(s.status == "failed" or s.has_error() for s in state.stories.values())
    # Safety-net worktree sweep (per-story cleanup happens in _story_finalize).
    from ash.config.settings import load_project

    try:
        project = load_project(state.project)
        seen: set[str] = set()
        for s in state.stories.values():
            for wt in (s.research.worktree_path, s.coding.worktree_path):
                if wt and wt not in seen:
                    seen.add(wt)
                    await _remove_worktree(project, wt)
    except Exception:  # noqa: BLE001
        pass
    return {"status": "failed" if (run_errored or stories_errored) else "completed"}


def _route_after_intake(state: WorkflowState) -> str:
    """Fail fast if intake couldn't fetch the issue. raw_to_dev skips PM+RFC (straight to story
    planning); raw_to_spec and spec_ready route through PM."""
    if state.intake.error:
        return "merge"
    return "plan_stories" if state.intake_mode == "raw_to_dev" else "pm"


def _build_story_subgraph(agents: dict[str, Agent]) -> Any:
    """The per-story build pipeline as a compiled subgraph over WorkflowState (no own
    checkpointer — the parent graph persists state and bubbles interrupts)."""
    sub: Any = StateGraph(WorkflowState)
    for name in STORY_BUILD_ORDER:
        sub.add_node(name, make_node(agents[name], node_name=name))
    sub.add_node("story_finalize", _story_finalize)
    sub.add_edge(START, "research")
    sub.add_edge("research", "coding")
    sub.add_edge("coding", "reviewer")
    sub.add_edge("reviewer", "fixer")
    sub.add_edge("fixer", "story_finalize")
    sub.add_edge("story_finalize", END)
    return sub.compile()


def build_graph(agents: dict[str, Agent], *, checkpointer: Any) -> Any:
    # langgraph's StateGraph generics are intricate; treat the builder handle as untyped.
    graph: Any = StateGraph(WorkflowState)

    graph.add_node("intake", make_node(agents["intake"], node_name="intake"))
    graph.add_node("pm", make_node(agents["pm"], node_name="pm"))
    graph.add_node("pm_publish", make_node(agents["pm_publish"], node_name="pm_publish"))
    graph.add_node("rfc", make_node(agents["rfc"], node_name="rfc"))
    graph.add_node("plan_stories", _plan_stories)
    graph.add_node("story_router", _story_router)
    graph.add_node("story_build", _build_story_subgraph(agents))
    graph.add_node("merge", _merge)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake",
        _route_after_intake,
        {"pm": "pm", "plan_stories": "plan_stories", "merge": "merge"},
    )
    graph.add_edge("pm", "pm_publish")
    graph.add_edge("pm_publish", "rfc")
    graph.add_edge("rfc", "plan_stories")
    graph.add_edge("plan_stories", "story_router")
    graph.add_conditional_edges(
        "story_router",
        _route_story,
        {"build": "story_build", "done": "merge"},
    )
    graph.add_edge("story_build", "story_router")
    graph.add_edge("merge", END)

    return graph.compile(checkpointer=checkpointer)
