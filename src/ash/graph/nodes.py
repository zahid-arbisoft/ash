"""Node adapter — wraps `agent.run` for LangGraph and captures errors into the agent's namespace.

A node never crashes the run: on exception it records the error in the agent's sub-state and lets
the graph advance to `merge`, which marks the run `failed` (plan §9 error handling).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ash.graph.state import WorkflowState

logger = logging.getLogger(__name__)


class Agent(Protocol):
    name: str

    async def run(self, state: WorkflowState) -> dict[str, Any]: ...


def make_node(agent: Agent) -> Callable[[WorkflowState], Awaitable[dict[str, Any]]]:
    async def node(state: WorkflowState) -> dict[str, Any]:
        run_id = state.run_id
        logger.info("agent=%s run_id=%s starting", agent.name, run_id)
        t0 = time.perf_counter()
        try:
            result = await agent.run(state)
            elapsed = time.perf_counter() - t0
            ns = result.get(agent.name, {})
            if isinstance(ns, dict) and ns.get("error"):
                logger.warning(
                    "agent=%s run_id=%s error elapsed=%.2fs: %s",
                    agent.name, run_id, elapsed, ns["error"],
                )
            else:
                logger.info("agent=%s run_id=%s done elapsed=%.2fs", agent.name, run_id, elapsed)
            return result
        except Exception as exc:  # noqa: BLE001 — record, never crash the run
            elapsed = time.perf_counter() - t0
            logger.error(
                "agent=%s run_id=%s exception elapsed=%.2fs",
                agent.name, run_id, elapsed, exc_info=True,
            )
            return {agent.name: {"error": f"{type(exc).__name__}: {exc}"}}

    return node
