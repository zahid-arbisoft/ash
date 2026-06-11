from langgraph.checkpoint.memory import MemorySaver

from ash.graph.builder import build_graph
from ash.graph.state import WorkflowState


class StubAgent:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        if self.name == "pm":
            return {"issue_title": "pm ran"}
        return {self.name: {"note": f"{self.name} ran"}}


def _agents():
    return {n: StubAgent(n) for n in ("pm", "research", "coding", "reviewer", "fixer")}


def _as_dict(result):
    return result if isinstance(result, dict) else result.model_dump()


async def test_graph_traverses_all_nodes_and_completes():
    graph = build_graph(_agents(), checkpointer=MemorySaver())
    initial = WorkflowState(run_id="r1", project="plane", item_id="42")
    result = _as_dict(await graph.ainvoke(initial, config={"configurable": {"thread_id": "r1"}}))
    assert result["issue_title"] == "pm ran"
    assert result["fixer"]["note"] == "fixer ran"
    assert result["status"] == "completed"


async def test_graph_marks_failed_when_substate_has_error():
    agents = _agents()

    class FailingPM:
        name = "pm"

        async def run(self, state):
            return {"pm": {"error": "boom"}}

    agents["pm"] = FailingPM()
    graph = build_graph(agents, checkpointer=MemorySaver())
    result = _as_dict(
        await graph.ainvoke(
            WorkflowState(run_id="r2", project="plane", item_id="1"),
            config={"configurable": {"thread_id": "r2"}},
        )
    )
    assert result["status"] == "failed"
