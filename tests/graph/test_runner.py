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


class PMPublishStub:
    name = "pm"

    async def run(self, state):
        return {}


def _runner():
    agents = {
        n: StubAgent(n)
        for n in ("intake", "pm", "rfc", "research", "coding", "reviewer", "fixer")
    }
    agents["pm_publish"] = PMPublishStub()
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


class FlakyAgent:
    """Fails the first time it runs, succeeds afterwards — to exercise retry-from-step."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    async def run(self, state):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom on first run")
        return {self.name: {"note": "recovered"}}


def test_first_failed_step_picks_earliest():
    # Run-level steps only (intake/pm/rfc); build steps are per-story now.
    runner = _runner()
    state = {"intake": {"note": "ok"}, "pm": {"error": "X"}, "rfc": {"error": "Y"}}
    assert runner.first_failed_step(state) == "pm"


def test_first_failed_step_none_when_clean():
    assert _runner().first_failed_step({"intake": {"note": "ok"}}) is None


def test_first_failed_story_picks_earliest():
    runner = _runner()
    state = {
        "story_order": ["T1", "T2"],
        "stories": {
            "T1": {"ticket_id": "T1", "status": "completed"},
            "T2": {"ticket_id": "T2", "status": "failed", "failed_step": "coding"},
        },
    }
    assert runner.first_failed_story(state) == ("T2", "coding")


async def test_retry_reruns_failed_story_to_completion():
    flaky = FlakyAgent("research")
    agents = {n: StubAgent(n) for n in ("intake", "pm", "rfc", "coding", "reviewer", "fixer")}
    agents["research"] = flaky
    agents["pm_publish"] = PMPublishStub()
    runner = Runner(graph=build_graph(agents, checkpointer=MemorySaver()))

    run_id = await runner.start_run(project="plane", item_id="42", wait=True)
    failed = await runner.get_run(run_id)
    assert failed["status"] == "failed"
    story = failed["stories"]["_main"]
    assert story["research"]["error"] is not None
    assert runner.first_failed_story(failed) == ("_main", "research")

    # Retry: research now succeeds → story + run complete, error cleared.
    state = await runner.retry_run(run_id, wait=True)
    assert state["status"] == "completed"
    story = state["stories"]["_main"]
    assert story["research"].get("error") is None
    assert story["research"]["note"] == "recovered"
    assert story["status"] == "completed"
    assert flaky.calls == 2  # ran once (failed) + once (retry)


async def test_regenerate_specific_story_step_reruns_only_that_story():
    # F4: explicit per-story regenerate re-runs the chosen story's step (here: coding).
    counts = {"coding": 0}

    class CountingCoding:
        name = "coding"

        async def run(self, state):
            counts["coding"] += 1
            return {"coding": {"note": "built", "pr_url": "https://gh/pr/1"}}

    agents = {n: StubAgent(n) for n in ("intake", "pm", "rfc", "research", "reviewer", "fixer")}
    agents["coding"] = CountingCoding()
    agents["pm_publish"] = PMPublishStub()
    runner = Runner(graph=build_graph(agents, checkpointer=MemorySaver()))

    run_id = await runner.start_run(project="plane", item_id="9", wait=True)
    done = await runner.get_run(run_id)
    assert done["status"] == "completed"
    assert counts["coding"] == 1

    # Regenerate the single story's coding step → coding runs again, no new run created.
    state = await runner.retry_run(run_id, ticket_id="_main", from_step="coding", wait=True)
    assert state["status"] == "completed"
    assert counts["coding"] == 2
    # PR is preserved on the story (no duplicate identity).
    assert state["stories"]["_main"]["pr_url"] == "https://gh/pr/1"


async def test_get_run_state_is_json_serializable_with_spec():
    agents = {n: StubAgent(n) for n in ("intake", "rfc", "research", "coding", "reviewer", "fixer")}
    agents["pm"] = SpecPM()
    agents["pm_publish"] = PMPublishStub()
    runner = Runner(graph=build_graph(agents, checkpointer=MemorySaver()))
    run_id = await runner.start_run(project="plane", item_id="42", wait=True)
    state = await runner.get_run(run_id)
    assert state is not None
    # the PM spec must be plain JSON (no Spec objects leaking) — this is what the UI/API serialize
    json.dumps(state)
    assert state["pm"]["spec"]["epic"]["title"] == "t"
