"""Runner — starts graph runs (background or awaited) and reads status from the checkpointer.

Shared by the FastAPI background task and any scheduler. The queue/worker swap-in point lives here:
replace `asyncio.create_task` with an enqueue call without touching the API or graph.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from ash.schemas import Spec

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
    def __init__(self, *, graph: Any, pm_agent: Any = None) -> None:
        self._graph = graph
        # PMAgent instance used by the workbench per-story refine (decision #29). Optional so
        # tests that build a bare graph keep working; refine_ticket no-ops when it's absent.
        self._pm_agent = pm_agent
        self._tasks: set[asyncio.Task[Any]] = set()
        # Per-run background task, so a run can be stopped (cancelled) by id. Single-process
        # only — in a multi-worker deploy the task may live on another worker (see stop_run).
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}

    def _spawn(self, run_id: str, coro: Any) -> None:
        """Run `coro` in the background, tracked by run_id so `stop_run` can cancel it."""
        task = asyncio.create_task(coro)
        self._run_tasks[run_id] = task
        self._tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if self._run_tasks.get(run_id) is t:
                self._run_tasks.pop(run_id, None)

        task.add_done_callback(_done)

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
        pm_only: bool = False,
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
            pm_only=pm_only,
        )

        async def _invoke() -> None:
            await self._graph.ainvoke(initial, config=self._config(run_id))
            await self._sync_status(run_id)

        if wait:
            await _invoke()
        else:
            self._spawn(run_id, _invoke())
        return run_id

    async def resume_run(
        self, run_id: str, decision: Any, *, background: bool = False
    ) -> dict[str, Any] | None:
        """Resume a run paused at a human-in-the-loop interrupt with the human's decision.

        `background=True` drives the graph in a tracked task and returns immediately (so a
        triggered agent doesn't block the HTTP request and can be stopped mid-run); the UI
        catches up over SSE. Default awaits to completion (used by the API + tests)."""
        config = self._config(run_id)

        async def _do() -> None:
            await self._graph.ainvoke(Command(resume=decision), config=config)
            await self._sync_status(run_id)

        if background:
            self._spawn(run_id, _do())
        else:
            await _do()
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
            update: dict[str, Any] = {run_step: _fresh_substate(run_step), "status": "running"}
            # Re-running PM seeds a new spec → clear old stories so the new spec produces a
            # fresh story set and the UI doesn't show stale completed/failed story cards.
            if run_step == "pm":
                update.update({"stories": {}, "story_order": [], "current_story": ""})
            await self._graph.aupdate_state(
                config,
                update,
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

    async def regenerate_spec(
        self,
        run_id: str,
        *,
        feedback: str,
        story_mode: str | None = None,
        wait: bool = False,
    ) -> dict[str, Any] | None:
        """PM workbench (decision #29): re-run PM with the reviewer's spec-level feedback.

        Seeds a fresh PMState carrying the feedback (consumed once by PMAgent.run) and an
        incremented iteration counter, clears any prior story planning, then forks the graph
        ``as_node="intake"`` so `pm → pm_publish` runs again and re-interrupts at the review gate.
        Cannot reuse ``retry_run(from_step="pm")`` — its ``_fresh_substate("pm")`` would wipe the
        feedback before PM reads it. ``pm_only``/``intake_mode`` live on WorkflowState so they
        survive the update; an optional ``story_mode`` lets the workbench toggle apply on regen.
        """
        current = await self.get_run(run_id)
        if current is None:
            return None
        config = self._config(run_id)
        prior = int((current.get("pm") or {}).get("regeneration_count", 0) or 0)
        update: dict[str, Any] = {
            "pm": PMState(feedback=feedback, regeneration_count=prior + 1),
            "stories": {},
            "story_order": [],
            "current_story": "",
            "status": "running",
        }
        if story_mode in ("single", "multiple"):
            update["story_mode"] = story_mode
        await self._graph.aupdate_state(config, update, as_node="intake")
        return await self._drive(run_id, config, wait)

    async def refine_ticket(
        self, run_id: str, *, ticket_id: str, feedback: str
    ) -> dict[str, Any] | None:
        """PM workbench (decision #29): refine ONE ticket of the current spec in place.

        Runs a single-ticket elaborate (reusing PMAgent) with the reviewer's feedback and folds the
        updated ticket back into ``pm.spec`` via ``aupdate_state`` WITHOUT advancing the graph — the
        run stays paused at the ``spec_review`` interrupt so the user can still Approve afterward.
        """
        current = await self.get_run(run_id)
        if current is None:
            return None
        if self._pm_agent is None:
            return current
        pm_dict = current.get("pm") or {}
        spec_dict = pm_dict.get("spec")
        if not spec_dict:
            return current
        spec = Spec.model_validate(spec_dict)
        getattr(self._pm_agent, "reset_exchanges", lambda: None)()
        refined = await self._pm_agent.refine_ticket(spec, ticket_id, feedback)
        if refined is None:
            return current
        spec.tickets = [refined if t.id == ticket_id else t for t in spec.tickets]
        pm_state = PMState.model_validate(pm_dict)
        pm_state.spec = spec
        pm_state.ticket_feedback = {**pm_state.ticket_feedback, ticket_id: feedback}
        # No as_node → patch the pm channel in place; the pending spec_review interrupt is kept.
        await self._graph.aupdate_state(self._config(run_id), {"pm": pm_state})
        # This PM call ran outside the node wrapper — persist its LLM I/O here (decision #30).
        await self._record_refine_exchanges(run_id, current.get("project", ""), ticket_id)
        return await self.get_run(run_id)

    async def _record_refine_exchanges(
        self, run_id: str, project: str, ticket_id: str
    ) -> None:
        """Persist LLM exchanges captured during a workbench refine (best-effort)."""
        exchanges = list(getattr(self._pm_agent, "_exchanges", []) or [])
        if not exchanges:
            return
        for ex in exchanges:
            ex["phase"] = "refine"
        try:
            from ash.db.base import get_sessionmaker
            from ash.db.exchanges import record_exchanges

            async with get_sessionmaker()() as session:
                await record_exchanges(
                    session,
                    run_id=run_id,
                    project=project,
                    ticket_id=ticket_id or None,
                    agent_name="pm",
                    exchanges=exchanges,
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — best-effort
            pass

    async def _drive(
        self, run_id: str, config: dict[str, Any], wait: bool
    ) -> dict[str, Any] | None:
        async def _resume() -> None:
            await self._graph.ainvoke(None, config=config)
            await self._sync_status(run_id)

        if wait:
            await _resume()
        else:
            self._spawn(run_id, _resume())
        return await self.get_run(run_id)

    async def stop_run(self, run_id: str) -> dict[str, Any] | None:
        """Stop a running run: cancel its in-flight background task and mark it `cancelled`.

        The LangGraph checkpoint is preserved at the last completed node, so the run can be
        resumed later (`resume_stopped`) or a story re-run from the per-story controls.
        Single-process only — if the task runs on another worker this just marks the status.
        """
        task = self._run_tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):  # await the cancellation to settle
                await task
        with contextlib.suppress(Exception):  # mark cancelled in the checkpoint (best-effort)
            await self._graph.aupdate_state(self._config(run_id), {"status": "cancelled"})
        await self._sync_status(run_id)
        return await self.get_run(run_id)

    async def resume_stopped(
        self, run_id: str, *, wait: bool = False
    ) -> dict[str, Any] | None:
        """Resume a previously stopped (cancelled) run from its last checkpoint."""
        config = self._config(run_id)
        await self._graph.aupdate_state(config, {"status": "running"})
        return await self._drive(run_id, config, wait)

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
        #
        # IMPORTANT: suppress the interrupt overlay when a background task is ACTIVELY executing
        # for this run.  LangGraph only clears snapshot.interrupts once the interrupted node
        # COMPLETES (i.e. after the LLM call finishes), which can take 5–30 s.  Without this
        # guard the SSE stream keeps re-surfacing the Trigger/Review button while the agent is
        # working, making it look like the click had no effect and tempting users to click again.
        active_task = self._run_tasks.get(run_id)
        task_is_running = active_task is not None and not active_task.done()
        if snapshot.interrupts and not task_is_running:
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
        elif not task_is_running and "pm_publish" in (getattr(snapshot, "next", None) or ()):
            # The spec gate is still pending but the interrupt payload was dropped by an in-place
            # state patch (the workbench per-story refine calls aupdate_state without advancing the
            # graph). pm_publish is always the spec_review gate, so re-surface it for the UI.
            state["status"] = "awaiting_review"
            state["pending_review"] = True
            state["pending_story"] = state.get("current_story", "")
        return state
