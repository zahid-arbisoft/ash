"""Runner — starts graph runs (background or awaited) and reads status from the checkpointer.

Shared by the FastAPI background task and any scheduler. The queue/worker swap-in point lives here:
replace `asyncio.create_task` with an enqueue call without touching the API or graph.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

from langgraph.types import Command
from pydantic_core import to_jsonable_python

from ash.graph.state import (
    CodingState,
    FixerState,
    PMState,
    ResearchState,
    ReviewerState,
    RFCState,
    StoryState,
    WorkflowState,
)

# Per-story build steps, in execution order (decision #26).
_STORY_STEPS: tuple[str, ...] = ("research", "coding", "reviewer", "fixer")


def _fresh_substate(step: str) -> Any:
    """A clean sub-state for the run-level step being retried (clears any prior error)."""
    return {
        "pm": PMState,
        "rfc": RFCState,
        "research": ResearchState,
        "coding": CodingState,
        "reviewer": ReviewerState,
        "fixer": FixerState,
    }[step]()


def _reset_story_from(story_dict: dict[str, Any], step: str) -> StoryState:
    """Return a copy of a story with namespaces from `step` onward cleared, status→pending.

    Preserves `branch`/`pr_url` so a regenerate updates the SAME PR (no duplicate)."""
    story = StoryState.model_validate(story_dict)
    fresh = {
        "research": ResearchState,
        "coding": CodingState,
        "reviewer": ReviewerState,
        "fixer": FixerState,
    }
    start = _STORY_STEPS.index(step)
    for s in _STORY_STEPS[start:]:
        setattr(story, s, fresh[s]())
    story.status = "pending"
    story.failed_step = None
    return story


class Runner:
    def __init__(self, *, graph: Any) -> None:
        self._graph = graph
        self._tasks: set[asyncio.Task[Any]] = set()

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    async def start_run(
        self,
        *,
        project: str,
        item_id: str,
        board: str = "github",
        intake_mode: str = "raw_to_spec",
        integration_id: int | None = None,
        attachments: list[str] | None = None,
        task_sink_id: int | None = None,
        ticket_id: str = "",
        story_mode: str = "single",
        wait: bool = False,
    ) -> str:
        run_id = uuid.uuid4().hex
        initial = WorkflowState(
            run_id=run_id,
            project=project,
            item_id=item_id,
            board=board,
            intake_mode=intake_mode,  # type: ignore[arg-type]
            integration_id=integration_id,
            attachments=attachments or [],
            task_sink_id=task_sink_id,
            ticket_id=ticket_id,
            story_mode=story_mode,  # type: ignore[arg-type]
        )

        async def _invoke() -> None:
            await self._graph.ainvoke(initial, config=self._config(run_id))
            await self._sync_status(run_id)

        if wait:
            await _invoke()
        else:
            task = asyncio.create_task(_invoke())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return run_id

    async def resume_run(self, run_id: str, decision: Any) -> dict[str, Any] | None:
        """Resume a run paused at a human-in-the-loop interrupt with the human's decision."""
        await self._graph.ainvoke(Command(resume=decision), config=self._config(run_id))
        await self._sync_status(run_id)
        return await self.get_run(run_id)

    # Run-level steps (not per-story). pm_publish shares the "pm" namespace.
    _RUN_STEP_ORDER = ("intake", "pm", "rfc")

    # To re-run a run-level step X we tell LangGraph "as if <predecessor> just finished".
    _RETRY_AS_NODE = {"pm": "intake", "rfc": "pm_publish"}

    def first_failed_step(self, state: dict[str, Any]) -> str | None:
        """Return the earliest run-level step (intake/pm/rfc) whose namespace errored, or None."""
        for step in self._RUN_STEP_ORDER:
            ns = state.get(step)
            err = ns.get("error") if isinstance(ns, dict) else getattr(ns, "error", None)
            if err:
                return step
        return None

    def first_failed_story(self, state: dict[str, Any]) -> tuple[str, str] | None:
        """Return ``(ticket_id, step)`` for the earliest story (in story_order) that failed, with
        the sub-step that errored. None when no story failed."""
        stories = state.get("stories") or {}
        order = state.get("story_order") or list(stories.keys())
        for tid in order:
            s = stories.get(tid)
            if not isinstance(s, dict):
                continue
            failed = s.get("failed_step")
            if s.get("status") == "failed" or failed:
                step = failed or next(
                    (
                        st
                        for st in _STORY_STEPS
                        if isinstance(s.get(st), dict) and s[st].get("error")
                    ),
                    "research",
                )
                return tid, step
        return None

    async def retry_run(
        self,
        run_id: str,
        *,
        from_step: str | None = None,
        ticket_id: str | None = None,
        wait: bool = False,
    ) -> dict[str, Any] | None:
        """Re-run a failed run from the earliest failure, or — when ``ticket_id`` is given — a
        specific story from ``from_step`` (also used for manual regenerate, F4).

        Run-level failures (intake/pm/rfc) fork via ``update_state(as_node=<predecessor>)``.
        Story failures reset that story (namespaces from the step onward, preserving branch/pr_url
        so the PR is updated not duplicated), set status→pending, and re-enter the story loop via
        ``as_node="plan_stories"`` so `story_router` picks the pending story up — completed stories
        are skipped, so the run resumes exactly at the failed/selected story.
        """
        current = await self.get_run(run_id)
        if current is None:
            return None
        config = self._config(run_id)

        # ── explicit per-story (retry a specific story / regenerate) ──────────
        if ticket_id is not None:
            return await self._retry_story(
                run_id, current, config, ticket_id, from_step or "research", wait
            )

        # ── run-level failure (intake/pm/rfc) ─────────────────────────────────
        run_step = (
            from_step if from_step in self._RETRY_AS_NODE else self.first_failed_step(current)
        )
        if run_step and run_step in self._RETRY_AS_NODE:
            await self._graph.aupdate_state(
                config,
                {run_step: _fresh_substate(run_step), "status": "running"},
                as_node=self._RETRY_AS_NODE[run_step],
            )
            return await self._drive(run_id, config, wait)

        # ── story failure (default) ───────────────────────────────────────────
        failed = self.first_failed_story(current)
        if failed is None:
            return current  # nothing to retry
        tid, step = failed
        return await self._retry_story(run_id, current, config, tid, step, wait)

    async def _retry_story(
        self,
        run_id: str,
        current: dict[str, Any],
        config: dict[str, Any],
        ticket_id: str,
        step: str,
        wait: bool,
    ) -> dict[str, Any] | None:
        stories = current.get("stories") or {}
        story_dict = stories.get(ticket_id)
        if not isinstance(story_dict, dict):
            return current
        reset = _reset_story_from(story_dict, step if step in _STORY_STEPS else "research")
        await self._graph.aupdate_state(
            config,
            {"stories": {ticket_id: reset}, "current_story": "", "status": "running"},
            as_node="plan_stories",
        )
        return await self._drive(run_id, config, wait)

    async def _drive(
        self, run_id: str, config: dict[str, Any], wait: bool
    ) -> dict[str, Any] | None:
        async def _resume() -> None:
            await self._graph.ainvoke(None, config=config)
            await self._sync_status(run_id)

        if wait:
            await _resume()
        else:
            task = asyncio.create_task(_resume())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return await self.get_run(run_id)

    async def _sync_status(self, run_id: str) -> None:
        """Copy the final run status onto the RunRecord for the runs-list badge (best-effort)."""
        from ash.db.runs import update_run_status

        try:
            state = await self.get_run(run_id)
            if state:
                await update_run_status(run_id, str(state.get("status", "running")))
        except Exception:  # noqa: BLE001 — denormalized status is best-effort
            pass

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        snapshot = await self._graph.aget_state(self._config(run_id))
        if not snapshot or not snapshot.values:
            return None
        # LangGraph may hand back namespaces as pydantic instances, as dicts, or as dicts that still
        # wrap pydantic objects (e.g. a Spec inside `pm`). Deep-convert to JSON-safe primitives
        # (enums -> values, datetimes -> iso, models -> dicts) so the API and the UI's tojson work.
        state = cast(dict[str, Any], to_jsonable_python(snapshot.values))
        # Overlay UI-facing fields from the HITL interrupt payload (not stored in graph state).
        # Three distinct interrupt reasons need different UI surfaces:
        #   "spec_review"    → PM spec gate   → Approve / Reject in run timeline
        #   "manual_trigger" → trigger gate   → "Trigger <agent>" button
        #   "merge_approval" → merge gate     → "Approve merge" button
        if snapshot.interrupts:
            payload = snapshot.interrupts[0].value
            # Which story (if any) is paused — so the UI surfaces the gate on the right card.
            state["pending_story"] = state.get("current_story", "")
            if payload == "spec_review" or (
                isinstance(payload, dict) and payload.get("reason") == "spec_review"
            ):
                state["status"] = "awaiting_review"
                state["pending_review"] = True
            elif isinstance(payload, dict) and payload.get("reason") == "manual_trigger":
                state["status"] = "awaiting_trigger"
                state["pending_trigger"] = payload.get("agent", "")
            elif isinstance(payload, dict) and payload.get("reason") == "merge_approval":
                state["status"] = "awaiting_merge"
                state["pending_merge"] = True
            else:
                # Fallback: treat any unknown interrupt as a generic review gate.
                state["status"] = "awaiting_review"
                state["pending_review"] = True
        return state
