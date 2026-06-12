"""Node adapter — wraps `agent.run` for LangGraph and captures errors into the agent's namespace.

A node never crashes the run: on exception it records the error in the agent's sub-state and lets
the graph advance to `merge`, which marks the run `failed` (plan §9 error handling).
The Langfuse callback is attached to the graph run config in Runner._config so it propagates
automatically to all LangChain calls within the run without per-node wiring.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import structlog
from structlog.contextvars import bind_contextvars

from ash.graph.state import WorkflowState

logger = structlog.get_logger(__name__)


class Agent(Protocol):
    name: str

    async def run(self, state: WorkflowState) -> dict[str, Any]: ...


def make_node(agent: Agent) -> Callable[[WorkflowState], Awaitable[dict[str, Any]]]:
    async def node(state: WorkflowState) -> dict[str, Any]:
        bind_contextvars(agent=agent.name)
        t0 = time.perf_counter()
        logger.info("agent_start")
        try:
            result = await agent.run(state)
            elapsed = time.perf_counter() - t0
            ns = result.get(agent.name, {})
            if isinstance(ns, dict) and ns.get("error"):
                logger.warning("agent_error", elapsed=round(elapsed, 2), error=ns["error"])
            else:
                logger.info("agent_done", elapsed=round(elapsed, 2))
            return result
        except Exception as exc:  # noqa: BLE001 — record, never crash the run
            elapsed = time.perf_counter() - t0
            logger.exception("agent_exception", elapsed=round(elapsed, 2))
            return {agent.name: {"error": f"{type(exc).__name__}: {exc}"}}

    return node
