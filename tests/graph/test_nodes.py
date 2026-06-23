from ash.graph.nodes import make_node
from ash.graph.state import StoryState, WorkflowState


class OkAgent:
    name = "dev"

    async def run(self, state):
        return {"dev": {"note": "done"}}


class BoomAgent:
    name = "dev"

    async def run(self, state):
        raise RuntimeError("kaboom")


class OkPM:
    name = "pm"

    async def run(self, state):
        return {"pm": {"note": "spec done"}}


def _story_state():
    """A state positioned on a single story (build-team nodes are story-scoped)."""
    return WorkflowState(
        run_id="r",
        project="plane",
        item_id="1",
        current_story="T1",
        stories={"T1": StoryState(ticket_id="T1", status="running")},
    )


async def test_scoped_node_folds_result_into_story():
    # Build-team nodes (coding) write into stories[current_story], not the flat namespace.
    node = make_node(OkAgent(), node_name="dev")
    update = await node(_story_state())
    assert update["stories"]["T1"].dev.note == "done"


async def test_scoped_node_captures_error_into_story():
    node = make_node(BoomAgent(), node_name="dev")
    update = await node(_story_state())
    story = update["stories"]["T1"]
    assert "kaboom" in story.dev.error
    assert story.status == "failed"
    assert story.failed_step == "dev"


class CombinedDevAgent:
    """A scoped Dev that also returns RUN-LEVEL keys (F7 combined-PR identity)."""

    name = "dev"

    async def run(self, state):
        return {
            "dev": {"note": "built", "pr_url": "https://gh/pr/combined"},
            "combined_branch": "agent/issue-run-r-combined",
            "combined_worktree": "/tmp/wt",
            "combined_pr_url": "https://gh/pr/combined",
        }


async def test_scoped_node_passes_run_level_keys_through():
    # F7: a story-scoped agent may also return run-level keys; they must survive _fold_story.
    node = make_node(CombinedDevAgent(), node_name="dev")
    update = await node(_story_state())
    assert update["stories"]["T1"].dev.pr_url == "https://gh/pr/combined"
    assert update["combined_branch"] == "agent/issue-run-r-combined"
    assert update["combined_worktree"] == "/tmp/wt"
    assert update["combined_pr_url"] == "https://gh/pr/combined"


async def test_run_level_node_passes_update_through():
    # Run-level nodes (pm) keep their flat namespace — not story-scoped.
    node = make_node(OkPM(), node_name="pm")
    update = await node(WorkflowState(run_id="r", project="plane", item_id="1"))
    assert update["pm"]["note"] == "spec done"
