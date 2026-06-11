"""Graph builder — wires PM → Research → Coding → Reviewer → Fixer → Merge.

Linear edges (no dynamic router yet, plan Phase 2). The `merge` terminal node sets `status`:
`failed` if any namespace carries an error, else `completed`.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ash.graph.nodes import Agent, make_node
from ash.graph.state import WorkflowState

AGENT_ORDER = ("pm", "research", "coding", "reviewer", "fixer")


async def _merge(state: WorkflowState) -> dict[str, Any]:
    errored = any(
        sub.error is not None
        for sub in (state.pm, state.research, state.coding, state.reviewer, state.fixer)
    )
    return {"status": "failed" if errored else "completed"}


def build_graph(agents: dict[str, Agent], *, checkpointer: Any) -> Any:
    # langgraph's StateGraph generics are intricate; treat the builder handle as untyped.
    graph: Any = StateGraph(WorkflowState)
    for name in AGENT_ORDER:
        graph.add_node(name, make_node(agents[name]))
    graph.add_node("merge", _merge)

    graph.add_edge(START, "pm")
    graph.add_edge("pm", "research")
    graph.add_edge("research", "coding")
    graph.add_edge("coding", "reviewer")
    graph.add_edge("reviewer", "fixer")
    graph.add_edge("fixer", "merge")
    graph.add_edge("merge", END)

    return graph.compile(checkpointer=checkpointer)
