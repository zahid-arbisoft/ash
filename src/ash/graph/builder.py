"""Graph builder — Intake → (PM?) → RFC? → plan_stories → [story loop] → Merge (decision #26).

Intake fetches the issue; a **conditional edge** routes by `intake_mode`:
- `raw_to_spec`  → PM converts the issue to a spec; RFC (opt-in) → plan_stories.
- `spec_ready`   → the spec already exists; PM extracts tickets; RFC → plan_stories.
- `raw_to_dev`   → skip PM/RFC; plan_stories synthesizes one story from the raw issue.

`plan_stories` turns the spec (or raw issue) into one or more `StoryState`s (dependency-sorted).
The **story loop** then builds them **one at a time**:

    plan_stories → story_router ──(next story)──► research → dev → reviewer → fixer
                        ↑                                                          │
                        └──────────────── story_finalize ◄────────────────────────┘
                        └──────────(no story left)──────────► merge → END

Build nodes are inlined in the parent graph (NOT a compiled subgraph) so that `interrupt()` calls
inside each agent fire at the parent-graph level, where `Command(resume=…)` correctly resumes from
the interrupted node rather than restarting the whole pipeline. Each node is story-scoped via
`make_node` (see `graph/nodes.py`).

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
STORY_BUILD_ORDER = ("research", "dev", "reviewer", "fixer")


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
    # Combined-PR strategy (F7): all stories share one worktree, so DON'T remove it here — the next
    # story stacks onto it. The final `_merge` sweep cleans it up once the run ends.
    wt = None if state.pr_strategy == "single" else (
        story.research.worktree_path or story.dev.worktree_path
    )
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
            # `state.combined_worktree` is the shared worktree for the single-PR strategy (F7);
            # include it so the run's final sweep removes it (per-story finalize skips it).
            for wt in (s.research.worktree_path, s.dev.worktree_path, state.combined_worktree):
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


def _route_after_pm_publish(state: WorkflowState) -> str:
    """PM workbench routing (decision #29). Full pipeline runs (pm_only=False) always continue to
    RFC — unchanged behavior. A pm_only run STOPS after the spec unless the reviewer picked a
    manual follow-up at the gate: 'rfc' → generate an RFC, 'build' → build the first story."""
    if not state.pm_only:
        return "rfc"
    if state.pm.next_action == "rfc":
        return "rfc"
    if state.pm.next_action == "build":
        return "plan_stories"
    return "merge"


def _route_after_rfc(state: WorkflowState) -> str:
    """RFC is a terminal action in the workbench ('generate an RFC and stop'); full runs always
    continue to story planning."""
    if state.pm_only and state.pm.next_action == "rfc":
        return "merge"
    return "plan_stories"


def build_graph(agents: dict[str, Agent], *, checkpointer: Any) -> Any:
    # langgraph's StateGraph generics are intricate; treat the builder handle as untyped.
    graph: Any = StateGraph(WorkflowState)

    graph.add_node("intake", make_node(agents["intake"], node_name="intake"))
    graph.add_node("pm", make_node(agents["pm"], node_name="pm"))
    graph.add_node("pm_publish", make_node(agents["pm_publish"], node_name="pm_publish"))
    graph.add_node("rfc", make_node(agents["rfc"], node_name="rfc"))
    graph.add_node("plan_stories", _plan_stories)
    graph.add_node("story_router", _story_router)
    # Build pipeline inlined (no compiled subgraph — see module docstring).
    for name in STORY_BUILD_ORDER:
        graph.add_node(name, make_node(agents[name], node_name=name))
    graph.add_node("story_finalize", _story_finalize)
    graph.add_node("merge", _merge)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake",
        _route_after_intake,
        {"pm": "pm", "plan_stories": "plan_stories", "merge": "merge"},
    )
    graph.add_edge("pm", "pm_publish")
    graph.add_conditional_edges(
        "pm_publish",
        _route_after_pm_publish,
        {"rfc": "rfc", "plan_stories": "plan_stories", "merge": "merge"},
    )
    graph.add_conditional_edges(
        "rfc",
        _route_after_rfc,
        {"plan_stories": "plan_stories", "merge": "merge"},
    )
    graph.add_edge("plan_stories", "story_router")
    graph.add_conditional_edges(
        "story_router",
        _route_story,
        {"build": "research", "done": "merge"},
    )
    graph.add_edge("research", "dev")
    graph.add_edge("dev", "reviewer")
    graph.add_edge("reviewer", "fixer")
    graph.add_edge("fixer", "story_finalize")
    graph.add_edge("story_finalize", "story_router")
    graph.add_edge("merge", END)

    return graph.compile(checkpointer=checkpointer)
