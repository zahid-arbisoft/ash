"""Node adapter — wraps `agent.run` for LangGraph and captures errors into the agent's namespace.

A node never crashes the run: on exception it records the error in the agent's sub-state and lets
the graph advance to `merge`, which marks the run `failed` (plan §9 error handling).

Side-effect: each node creates / updates AgentTask rows for the per-agent task queue
(agent_task_dispatch_plan §3). This is always best-effort — task persistence failures never
block or crash the run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.errors import GraphInterrupt

from ash.graph.state import StoryState, WorkflowState

logger = logging.getLogger(__name__)

# Build-team nodes that operate on the CURRENT story rather than run-level state. Their flat
# namespace is hydrated from `stories[current_story]` before the agent runs and the result is
# folded back into that story afterwards (decision #26).
_SCOPED_STEPS: frozenset[str] = frozenset({"research", "dev", "reviewer", "fixer"})

# Map graph node name → the AgentTask agent_name it belongs to (for task tracking).
# "pm" and "pm_publish" both contribute to the single "pm" task.
_NODE_TASK_AGENT: dict[str, str] = {
    "intake": "pm",        # intake completing triggers PM task creation
    "pm": "pm",
    "pm_publish": "pm",
    "rfc": "rfc",
    "research": "research",
    "dev": "dev",
    "reviewer": "reviewer",
    "fixer": "fixer",
}

# When a node completes successfully, which agent's task to create next (None = no auto-create).
# reviewer → fixer is handled specially (only when verdict=request_changes).
_NEXT_TASK_AGENT: dict[str, str | None] = {
    "intake": None,           # pm task created after intake, but PM runs immediately
    "pm_publish": "research", # after spec approval: create research task
    "rfc": None,              # research task was already created by pm_publish
    "research": "dev",
    "dev": "reviewer",
    "reviewer": None,         # handled per-verdict below
    "fixer": "reviewer",      # fixer loops back to reviewer
}


def _task_title(state: WorkflowState) -> str:
    """Best-effort human-readable title from run state."""
    if state.pm.spec and state.pm.spec.epic:
        return state.pm.spec.epic.title
    return state.issue_title or state.item_id


async def _create_task_best_effort(
    state: WorkflowState,
    agent_name: str,
    status: str = "pending",
    max_retries: int = 0,
    ticket_id: str = "",
) -> None:
    """Create an AgentTask row if one doesn't already exist for this run × agent × story."""
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import upsert_agent_task

        async with get_sessionmaker()() as session:
            await upsert_agent_task(
                session,
                agent_name=agent_name,
                project=state.project,
                run_id=state.run_id,
                item_id=state.item_id,
                ticket_id=ticket_id,
                title=_task_title(state),
                max_retries=max_retries,
                status=status,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — task tracking is best-effort
        logger.debug("[nodes] task create best-effort failed agent=%s: %s", agent_name, exc)


async def _update_task_best_effort(
    state: WorkflowState,
    agent_name: str,
    status: str,
    result_ref: str | None = None,
    error: str | None = None,
    ticket_id: str = "",
) -> None:
    """Update an AgentTask row's status. No-op if the task doesn't exist."""
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import get_task_for_run, update_task_status

        async with get_sessionmaker()() as session:
            task = await get_task_for_run(session, state.run_id, agent_name, ticket_id)
            if task is None:
                return
            kwargs: dict[str, Any] = {}
            if result_ref is not None:
                kwargs["result_ref"] = result_ref
            if error is not None:
                kwargs["error"] = error
            await update_task_status(session, task.id, status, **kwargs)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[nodes] task update best-effort failed agent=%s: %s", agent_name, exc)


class Agent(Protocol):
    name: str

    async def run(self, state: WorkflowState) -> dict[str, Any]: ...


def _hydrate_story(state: WorkflowState) -> StoryState:
    """Copy the current story's build namespaces onto the flat scratch fields so the (story-
    agnostic) agent reads/writes them, and point `ticket_id` at the story so `brief()` is scoped."""
    current = state.current_story
    story = state.stories.get(current) or StoryState(ticket_id=current)
    state.research = story.research.model_copy(deep=True)
    state.dev = story.dev.model_copy(deep=True)
    state.reviewer = story.reviewer.model_copy(deep=True)
    state.fixer = story.fixer.model_copy(deep=True)
    # Preserve story-level PR identity onto the (possibly reset) dev scratch so a
    # regenerate/retry updates the SAME PR instead of opening a duplicate (decision #26 / F2).
    state.dev.branch = state.dev.branch or story.branch
    state.dev.pr_url = state.dev.pr_url or story.pr_url
    state.ticket_id = current
    return story


def _fold_story(state: WorkflowState, agent_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Fold an agent's namespace result into `stories[current_story]` and derive story-level
    fields (branch/pr_url/failed_step). Returns the graph update {"stories": {tid: story}}."""
    current = state.current_story
    base = state.stories.get(current) or StoryState(ticket_id=current)
    story = base.model_copy(deep=True)
    ns_update = result.get(agent_name, {})
    if isinstance(ns_update, dict) and ns_update:
        sub = getattr(story, agent_name)
        setattr(story, agent_name, sub.model_copy(update=ns_update))
    updated_sub = getattr(story, agent_name)
    branch = getattr(updated_sub, "branch", None)
    pr_url = getattr(updated_sub, "pr_url", None)
    if branch:
        story.branch = branch
    if pr_url:
        story.pr_url = pr_url
    if getattr(updated_sub, "error", None):
        story.status = "failed"
        story.failed_step = agent_name if agent_name in _SCOPED_STEPS else story.failed_step  # type: ignore[assignment]
    return {"stories": {current: story}}


def make_node(
    agent: Agent, node_name: str | None = None
) -> Callable[[WorkflowState], Awaitable[dict[str, Any]]]:
    """Wrap an agent for LangGraph. `node_name` is the graph node identifier (e.g. 'pm_publish').
    When provided, task lifecycle side-effects are recorded in the agent_tasks table.

    Build-team nodes (research/coding/reviewer/fixer) are **story-scoped**: the current story's
    namespaces are hydrated before the agent runs and the result is folded back into
    `stories[current_story]` (decision #26)."""
    _node = node_name or agent.name
    scoped = _node in _SCOPED_STEPS

    async def node(state: WorkflowState) -> dict[str, Any]:
        task_agent = _NODE_TASK_AGENT.get(_node)
        ticket_id = ""
        if scoped:
            _hydrate_story(state)
            ticket_id = state.current_story

        # ── pre-run: mark task in_progress (best-effort) ──────────────────────
        if task_agent and _node not in ("intake", "pm_publish"):
            # intake creates the PM task; pm_publish creates research task after approval.
            # For all other nodes: mark the existing task in_progress when we enter.
            await _update_task_best_effort(state, task_agent, "in_progress", ticket_id=ticket_id)

        # ── run the agent ──────────────────────────────────────────────────────
        # Reset the LLM-I/O capture buffer so we only persist this run's exchanges (decision #30).
        getattr(agent, "reset_exchanges", lambda: None)()
        started = time.monotonic()
        try:
            result = await agent.run(state)
        except GraphInterrupt:
            raise  # let LangGraph handle HITL interrupts — don't touch task status
        except Exception as exc:  # noqa: BLE001 — record error, never crash the run
            err = f"{type(exc).__name__}: {exc}"
            if task_agent:
                await _update_task_best_effort(
                    state, task_agent, "failed", error=err, ticket_id=ticket_id
                )
            await _record_metric(state, agent.name, ticket_id, started, {"error": err}, "failed")
            await _record_exchanges(state, agent, ticket_id)
            if scoped:
                return _fold_story(state, agent.name, {agent.name: {"error": err}})
            return {agent.name: {"error": err}}

        # ── analytics: tokens + duration + LLM I/O (best-effort) ───────────────
        ns_result = result.get(agent.name, {}) if isinstance(result, dict) else {}
        status = "failed" if isinstance(ns_result, dict) and ns_result.get("error") else "completed"
        await _record_metric(state, agent.name, ticket_id, started, ns_result, status)
        await _record_exchanges(state, agent, ticket_id)

        # ── post-run: update task + create next ───────────────────────────────
        if task_agent:
            await _handle_post_run(state, _node, task_agent, result, ticket_id=ticket_id)

        update = _fold_story(state, agent.name, result) if scoped else result
        # A story-scoped agent may also return RUN-LEVEL keys (e.g. Dev's combined-PR identity:
        # combined_branch/combined_worktree/combined_pr_url — F7). `_fold_story` only keeps the
        # agent's own namespace, so pass those extra top-level keys through explicitly.
        if scoped and isinstance(result, dict):
            for k, v in result.items():
                if k != agent.name and k not in update:
                    update[k] = v
        # Consume-once custom prompt (decision #33): once an agent has run with its custom prompt
        # folded in, drop it so a later forward pass / retry doesn't re-apply stale instructions.
        if isinstance(update, dict) and state.custom_prompts.get(agent.name):
            update["custom_prompts"] = {
                k: v for k, v in state.custom_prompts.items() if k != agent.name
            }
        return update

    return node


async def _record_metric(
    state: WorkflowState,
    agent_name: str,
    ticket_id: str,
    started: float,
    ns_result: Any,
    status: str,
) -> None:
    """Persist an AgentRunMetric row (tokens + duration + model). Best-effort (F8)."""
    duration_ms = int((time.monotonic() - started) * 1000)
    tokens = ns_result.get("tokens") if isinstance(ns_result, dict) else None
    prompt_tokens = int((tokens or {}).get("prompt_tokens", 0))
    completion_tokens = int((tokens or {}).get("completion_tokens", 0))
    try:
        from ash.config.settings import get_settings
        from ash.db.base import get_sessionmaker
        from ash.db.metrics import record_metric

        model = ""
        try:
            model = get_settings().model_for(agent_name).model
        except Exception:  # noqa: BLE001
            model = ""
        async with get_sessionmaker()() as session:
            await record_metric(
                session,
                run_id=state.run_id,
                project=state.project,
                ticket_id=ticket_id or None,
                agent_name=agent_name,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_ms=duration_ms,
                status=status,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — analytics are best-effort, never block a run
        logger.debug("[nodes] metric record best-effort failed agent=%s: %s", agent_name, exc)


async def _record_exchanges(state: WorkflowState, agent: Agent, ticket_id: str) -> None:
    """Persist the agent's captured LLM exchanges (prompt + response); best-effort (#30)."""
    exchanges = list(getattr(agent, "_exchanges", []) or [])
    if not exchanges:
        return
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.exchanges import record_exchanges

        async with get_sessionmaker()() as session:
            await record_exchanges(
                session,
                run_id=state.run_id,
                project=state.project,
                ticket_id=ticket_id or None,
                agent_name=agent.name,
                exchanges=exchanges,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — capture is best-effort, never block a run
        logger.debug("[nodes] exchange record best-effort failed agent=%s: %s", agent.name, exc)


async def _handle_post_run(
    state: WorkflowState,
    node_name: str,
    task_agent: str,
    result: dict[str, Any],
    ticket_id: str = "",
) -> None:
    """Best-effort: complete current task and create the next one if appropriate.

    Build-team tasks (research/coding/reviewer/fixer) are per-story (`ticket_id`); the first one
    (research) is created by `plan_stories`. Run-level tasks (intake/pm/rfc) use ticket_id="".
    """
    try:
        from ash.config.settings import load_project
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import get_task_for_run, mark_task_completed, upsert_agent_task

        async with get_sessionmaker()() as session:
            # ── intake: create PM task (in_progress — PM runs immediately) ────
            if node_name == "intake":
                if not result.get("intake", {}).get("error"):
                    await upsert_agent_task(
                        session,
                        agent_name="pm",
                        project=state.project,
                        run_id=state.run_id,
                        item_id=state.item_id,
                        title=_task_title(state),
                        max_retries=0,
                        status="in_progress",
                    )
                await session.commit()
                return

            # ── pm_publish: complete the PM task. Per-story research tasks are created by
            #    plan_stories (which knows the story set), not here. ─────────────
            if node_name == "pm_publish":
                pm_result = result.get("pm", {})
                if pm_result.get("note") and "cancelled" not in str(pm_result.get("note", "")):
                    task = await get_task_for_run(session, state.run_id, "pm")
                    if task:
                        await mark_task_completed(session, task.id)
                await session.commit()
                return

            # ── generic (per-story for build agents): complete + create next ──
            task = await get_task_for_run(session, state.run_id, task_agent, ticket_id)
            agent_result = result.get(task_agent, {})
            error_val = agent_result.get("error") if isinstance(agent_result, dict) else None

            if error_val:
                if task:
                    from ash.db.tasks import update_task_status
                    await update_task_status(session, task.id, "failed", error=error_val)
            else:
                # Derive a result_ref (PR url, doc path, etc.)
                result_ref: str | None = None
                if isinstance(agent_result, dict):
                    result_ref = (
                        agent_result.get("pr_url")
                        or agent_result.get("doc_ref")
                        or agent_result.get("board_ref")
                    )
                if task:
                    await mark_task_completed(session, task.id, result_ref=result_ref)

                # Create next task if defined
                next_agent = _NEXT_TASK_AGENT.get(node_name)

                # Special case: reviewer only creates fixer task on request_changes
                if node_name == "reviewer":
                    verdict = (
                        agent_result.get("verdict") if isinstance(agent_result, dict) else None
                    )
                    next_agent = "fixer" if verdict == "request_changes" else None

                if next_agent:
                    try:
                        project = load_project(state.project)
                        n_policy = project.agent_policy(next_agent)
                        n_status = "in_progress" if n_policy.trigger == "auto" else "pending"
                    except Exception:  # noqa: BLE001
                        n_status = "pending"
                        n_policy_max_retries = 0
                    else:
                        n_policy_max_retries = n_policy.max_retries
                    await upsert_agent_task(
                        session,
                        agent_name=next_agent,
                        project=state.project,
                        run_id=state.run_id,
                        item_id=state.item_id,
                        ticket_id=ticket_id,
                        title=_task_title(state),
                        max_retries=n_policy_max_retries,
                        status=n_status,
                    )

            await session.commit()
    except Exception as exc:  # noqa: BLE001 — task lifecycle is best-effort
        logger.debug("[nodes] post-run task lifecycle failed node=%s: %s", node_name, exc)
