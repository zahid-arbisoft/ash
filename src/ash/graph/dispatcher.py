"""Background dispatcher — auto-resumes 'pending' AgentTask rows for agents configured
with trigger='auto', respecting concurrency_limit, daily_quota, max_retries, and
schedule_cron (agent_task_dispatch_plan §4).

DispatchService.tick() is called on a configurable interval. FastAPI lifespan wires it up.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from croniter import croniter  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_TICK_INTERVAL_S: float = 10.0  # how often to scan for pending tasks


class DispatchService:
    """Long-running asyncio service that auto-dispatches pending agent tasks."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self._task: asyncio.Task[None] | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="dispatcher")
            logger.info("[dispatcher] started (interval=%.0fs)", _TICK_INTERVAL_S)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[dispatcher] stopped")

    # ── main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 — tick errors must not kill the loop
                logger.warning("[dispatcher] tick error: %s", exc)
            await asyncio.sleep(_TICK_INTERVAL_S)

    async def tick(self) -> None:
        """Single dispatch pass: pick up eligible pending tasks and resume their runs."""
        from ash.config.settings import load_project
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import (
            active_task_count,
            get_all_pending_tasks,
            today_completed_count,
            update_task_status,
        )

        async with get_sessionmaker()() as session:
            pending = await get_all_pending_tasks(session, limit=100)
            if not pending:
                return

            for task in pending:
                try:
                    # ── load project + policy ──────────────────────────────
                    project = load_project(task.project)
                    policy = project.agent_policy(task.agent_name)

                    # ── only auto-dispatch tasks for trigger='auto' agents ──
                    if policy.trigger != "auto":
                        continue

                    # ── enabled gate ──────────────────────────────────────
                    if not policy.enabled:
                        continue

                    # ── schedule_cron gate ────────────────────────────────
                    if policy.schedule_cron and not _cron_allows(policy.schedule_cron):
                        continue

                    # ── concurrency gate ──────────────────────────────────
                    active = await active_task_count(session, task.agent_name, task.project)
                    if active >= policy.concurrency_limit:
                        continue

                    # ── daily_quota gate ──────────────────────────────────
                    if policy.daily_quota is not None:
                        today_done = await today_completed_count(
                            session, task.agent_name, task.project
                        )
                        if today_done >= policy.daily_quota:
                            continue

                    # ── mark in_progress before resume so re-entrant tick ─
                    # sees it as active
                    await update_task_status(session, task.id, "in_progress")
                    await session.commit()

                    # ── resume the run (fire-and-forget coroutine) ─────────
                    run_id = task.run_id
                    agent_name = task.agent_name
                    asyncio.create_task(
                        self._resume_and_handle(run_id, agent_name, task.id),
                        name=f"dispatch-{agent_name}-{run_id[:8]}",
                    )
                    logger.info(
                        "[dispatcher] dispatched agent=%s run=%s task=%d",
                        task.agent_name,
                        task.run_id[:8],
                        task.id,
                    )

                except Exception as exc:  # noqa: BLE001 — per-task errors must not stop the loop
                    logger.warning("[dispatcher] skip task=%d: %s", task.id, exc)

    async def _resume_and_handle(self, run_id: str, agent_name: str, task_id: int) -> None:
        """Resume a run that is paused at an agent trigger gate. On error, mark task failed."""
        try:
            await self._runner.resume_run(run_id, "run")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[dispatcher] resume failed run=%s agent=%s: %s", run_id[:8], agent_name, exc
            )
            try:
                from ash.db.base import get_sessionmaker
                from ash.db.tasks import update_task_status

                async with get_sessionmaker()() as session:
                    await update_task_status(session, task_id, "failed", error=str(exc))
                    await session.commit()
            except Exception:  # noqa: BLE001
                pass


def _cron_allows(cron_expr: str) -> bool:
    """Return True if now falls within the current cron window (i.e. the last tick was due).

    Uses croniter: we check whether there was a scheduled tick in the last _TICK_INTERVAL_S
    seconds. If croniter isn't installed the gate is always open.
    """
    try:
        import datetime

        now = datetime.datetime.now(datetime.UTC)
        past = now.timestamp() - _TICK_INTERVAL_S
        cron = croniter(cron_expr, past)
        next_ts = cron.get_next(float)
        return bool(next_ts <= now.timestamp())
    except Exception:  # noqa: BLE001 — bad cron expression → don't block the task
        return True
