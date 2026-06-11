import json

from langgraph.checkpoint.memory import MemorySaver

from ash.graph.builder import build_graph
from ash.graph.runner import Runner
from ash.schemas import Epic, Spec, TechnicalSpec


class StubAgent:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        return {self.name: {"note": "ok"}} if self.name != "pm" else {"issue_title": "ok"}


class SpecPM:
    name = "pm"

    async def run(self, state):
        spec = Spec(
            epic=Epic(title="t", summary="s", business_goal="b", acceptance_criteria=[]),
            technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
            tickets=[],
        )
        return {"pm": {"spec": spec, "board_ref": "r"}}


def _runner():
    agents = {n: StubAgent(n) for n in ("intake", "pm", "research", "coding", "reviewer", "fixer")}
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


async def test_get_run_state_is_json_serializable_with_spec():
    agents = {n: StubAgent(n) for n in ("intake", "research", "coding", "reviewer", "fixer")}
    agents["pm"] = SpecPM()
    runner = Runner(graph=build_graph(agents, checkpointer=MemorySaver()))
    run_id = await runner.start_run(project="plane", item_id="42", wait=True)
    state = await runner.get_run(run_id)
    assert state is not None
    # the PM spec must be plain JSON (no Spec objects leaking) — this is what the UI/API serialize
    json.dumps(state)
    assert state["pm"]["spec"]["epic"]["title"] == "t"
