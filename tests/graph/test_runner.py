from langgraph.checkpoint.memory import MemorySaver

from ash.graph.builder import build_graph
from ash.graph.runner import Runner


class StubAgent:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        return {self.name: {"note": "ok"}} if self.name != "pm" else {"issue_title": "ok"}


def _runner():
    agents = {n: StubAgent(n) for n in ("pm", "research", "coding", "reviewer", "fixer")}
    return Runner(graph=build_graph(agents, checkpointer=MemorySaver()))


async def test_start_run_and_get_run():
    runner = _runner()
    run_id = await runner.start_run(project="plane", item_id="42", wait=True)
    status = await runner.get_run(run_id)
    assert status is not None
    assert status["status"] == "completed"
    assert status["item_id"] == "42"


async def test_get_run_unknown_returns_none():
    assert await _runner().get_run("nope") is None
