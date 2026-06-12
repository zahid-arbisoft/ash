"""Runner — starts graph runs (background or awaited) and reads status from the checkpointer.

Shared by the FastAPI background task and any scheduler. The queue/worker swap-in point lives here:
replace `asyncio.create_task` with an enqueue call without touching the API or graph.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

import structlog
from langgraph.types import Command
from pydantic_core import to_jsonable_python
from structlog.contextvars import bind_contextvars, clear_contextvars

from ash.graph.state import WorkflowState
from ash.observability.langfuse import get_langfuse_callback

logger = structlog.get_logger(__name__)


class Runner:
    def __init__(self, *, graph: Any) -> None:
        self._graph = graph
        self._tasks: set[asyncio.Task[Any]] = set()

    def _config(self, thread_id: str) -> dict[str, Any]:
        cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        cb = get_langfuse_callback()
        if cb is not None:
            cfg["callbacks"] = [cb]
        return cfg

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
        )

        async def _invoke() -> None:
            clear_contextvars()
            bind_contextvars(run_id=run_id, intake_mode=intake_mode)
            logger.info(
                "run_start", project=project, item_id=item_id, integration_id=integration_id
            )
            try:
                await self._graph.ainvoke(initial, config=self._config(run_id))
                logger.info("run_end", status="completed")
            except Exception:
                logger.exception("run_end", status="crashed")

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
        return await self.get_run(run_id)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        snapshot = await self._graph.aget_state(self._config(run_id))
        if not snapshot or not snapshot.values:
            return None
        # LangGraph may hand back namespaces as pydantic instances, as dicts, or as dicts that still
        # wrap pydantic objects (e.g. a Spec inside `pm`). Deep-convert to JSON-safe primitives
        # (enums -> values, datetimes -> iso, models -> dicts) so the API and the UI's tojson work.
        return cast(dict[str, Any], to_jsonable_python(snapshot.values))
