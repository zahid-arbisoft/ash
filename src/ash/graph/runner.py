"""Runner — starts graph runs (background or awaited) and reads status from the checkpointer.

Shared by the FastAPI background task and any scheduler. The queue/worker swap-in point lives here:
replace `asyncio.create_task` with an enqueue call without touching the API or graph.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ash.graph.state import WorkflowState


class Runner:
    def __init__(self, *, graph: Any) -> None:
        self._graph = graph
        self._tasks: set[asyncio.Task[Any]] = set()

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    async def start_run(
        self, *, project: str, item_id: str, board: str = "github", wait: bool = False
    ) -> str:
        run_id = uuid.uuid4().hex
        initial = WorkflowState(run_id=run_id, project=project, item_id=item_id, board=board)

        async def _invoke() -> None:
            await self._graph.ainvoke(initial, config=self._config(run_id))

        if wait:
            await _invoke()
        else:
            task = asyncio.create_task(_invoke())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return run_id

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        snapshot = await self._graph.aget_state(self._config(run_id))
        if not snapshot or not snapshot.values:
            return None
        values = snapshot.values
        if isinstance(values, dict):
            return values
        dumped: dict[str, Any] = values.model_dump()
        return dumped
