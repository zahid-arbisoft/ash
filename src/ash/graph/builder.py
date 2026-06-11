"""Graph builder — Intake → (PM?) → Research → Coding → Reviewer → Fixer → Merge.

Intake fetches the issue; a **conditional edge** then routes by `intake_mode`:
- `raw_to_spec`  → PM converts the issue to a spec, then the build team runs.
- `spec_ready`   → the spec already exists (parsed at intake); skip PM.
- `raw_to_dev`   → feed the raw issue straight to the build team; skip PM.

The `merge` terminal node sets `status`: `failed` if any namespace errored, else `completed`.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ash.graph.nodes import Agent, make_node
from ash.graph.state import WorkflowState

BUILD_ORDER = ("research", "coding", "reviewer", "fixer")


async def _merge(state: WorkflowState) -> dict[str, Any]:
    errored = any(
        sub.error is not None
        for sub in (
            state.intake,
            state.pm,
            state.research,
            state.coding,
            state.reviewer,
            state.fixer,
        )
    )
    return {"status": "failed" if errored else "completed"}


def _route_after_intake(state: WorkflowState) -> str:
    """raw_to_spec runs PM; spec_ready/raw_to_dev skip straight to the build team."""
    return "pm" if state.intake_mode == "raw_to_spec" else "research"


def build_graph(agents: dict[str, Agent], *, checkpointer: Any) -> Any:
    # langgraph's StateGraph generics are intricate; treat the builder handle as untyped.
    graph: Any = StateGraph(WorkflowState)

    graph.add_node("intake", make_node(agents["intake"]))
    graph.add_node("pm", make_node(agents["pm"]))
    for name in BUILD_ORDER:
        graph.add_node(name, make_node(agents[name]))
    graph.add_node("merge", _merge)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route_after_intake, {"pm": "pm", "research": "research"})
    graph.add_edge("pm", "research")
    graph.add_edge("research", "coding")
    graph.add_edge("coding", "reviewer")
    graph.add_edge("reviewer", "fixer")
    graph.add_edge("fixer", "merge")
    graph.add_edge("merge", END)

    return graph.compile(checkpointer=checkpointer)
