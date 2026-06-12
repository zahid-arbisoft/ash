"""P4 / P0-risk-#1: a run can pause at an interrupt and resume via the checkpointer."""

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ash.graph.runner import Runner


class _S(TypedDict):
    status: str


def _interrupting_graph():
    def ask(state: _S) -> dict[str, str]:
        decision = interrupt({"question": "approve merge?"})
        return {"status": f"resumed:{decision}"}

    g: StateGraph = StateGraph(_S)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    return g.compile(checkpointer=MemorySaver())


async def test_run_pauses_at_interrupt_then_resumes():
    graph = _interrupting_graph()
    runner = Runner(graph=graph)
    cfg = {"configurable": {"thread_id": "t1"}}

    # first invocation hits the interrupt and pauses (does not complete)
    result = await graph.ainvoke({"status": "start"}, config=cfg)
    assert "__interrupt__" in result

    # the human's decision resumes the same thread through the checkpointer
    state = await runner.resume_run("t1", "yes")
    assert state is not None
    assert state["status"] == "resumed:yes"
