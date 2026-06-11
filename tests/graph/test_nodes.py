from ash.graph.nodes import make_node
from ash.graph.state import WorkflowState


class OkAgent:
    name = "coding"

    async def run(self, state):
        return {"coding": {"note": "done"}}


class BoomAgent:
    name = "coding"

    async def run(self, state):
        raise RuntimeError("kaboom")


def _state():
    return WorkflowState(run_id="r", project="plane", item_id="1")


async def test_node_passes_update_through():
    node = make_node(OkAgent())
    update = await node(_state())
    assert update["coding"]["note"] == "done"


async def test_node_captures_error_into_namespace():
    node = make_node(BoomAgent())
    update = await node(_state())
    assert "kaboom" in update["coding"]["error"]
