"""Node adapter — wraps `agent.run` for LangGraph and captures errors into the agent's namespace.

A node never crashes the run: on exception it records the error in the agent's sub-state and lets
the graph advance to `merge`, which marks the run `failed` (plan §9 error handling).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ash.graph.state import WorkflowState


class Agent(Protocol):
    name: str

    async def run(self, state: WorkflowState) -> dict[str, Any]: ...


def make_node(agent: Agent) -> Callable[[WorkflowState], Awaitable[dict[str, Any]]]:
    async def node(state: WorkflowState) -> dict[str, Any]:
        try:
            return await agent.run(state)
        except Exception as exc:  # noqa: BLE001 — record, never crash the run
            return {agent.name: {"error": f"{type(exc).__name__}: {exc}"}}

    return node
