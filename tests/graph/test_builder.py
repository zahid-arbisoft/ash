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


class PMPublishStub:
    name = "pm"

    async def run(self, state):
        return {}


def _agents():
    agents = {
        n: StubAgent(n)
        for n in ("intake", "pm", "rfc", "research", "coding", "reviewer", "fixer")
    }
    agents["pm_publish"] = PMPublishStub()
    return agents


def _as_dict(result):
    return result if isinstance(result, dict) else result.model_dump()


async def test_graph_traverses_all_nodes_and_completes():
    graph = build_graph(_agents(), checkpointer=MemorySaver())
    initial = WorkflowState(run_id="r1", project="plane", item_id="42")
    result = _as_dict(await graph.ainvoke(initial, config={"configurable": {"thread_id": "r1"}}))
    assert result["issue_title"] == "pm ran"
    # No spec → one synthetic story; the build team runs scoped to it.
    story = result["stories"]["_main"]
    assert story.fixer.note == "fixer ran"
    assert story.status == "completed"
    assert result["status"] == "completed"


async def test_graph_fails_fast_on_intake_error():
    """When intake has an error the graph routes straight
    to merge without running PM or research."""
    agents = _agents()
    reached: list[str] = []

    class FailingIntake:
        name = "intake"

        async def run(self, state):
            reached.append("intake")
            return {"intake": {"error": "no issue source configured"}}

    class SpyPM:
        name = "pm"

        async def run(self, state):
            reached.append("pm")
            return {}

    agents["intake"] = FailingIntake()
    agents["pm"] = SpyPM()
    graph = build_graph(agents, checkpointer=MemorySaver())
    result = _as_dict(
        await graph.ainvoke(
            WorkflowState(run_id="r-fail-intake", project="plane", item_id="1"),
            config={"configurable": {"thread_id": "r-fail-intake"}},
        )
    )
    assert result["status"] == "failed"
    assert "intake" in reached
    assert "pm" not in reached  # PM must NOT run when intake errored


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
