from langgraph.checkpoint.memory import MemorySaver

from ash.agents.intake import IntakeAgent
from ash.config.settings import Settings
from ash.graph.builder import build_graph
from ash.graph.state import WorkflowState
from ash.integrations.base import RawIssue
from ash.schemas import Epic, Spec, TechnicalSpec


def _spec() -> Spec:
    return Spec(
        epic=Epic(title="t", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=[],
    )


class FakeProvider:
    kind = "github"

    def __init__(self, body: str) -> None:
        self._body = body

    async def fetch_issue(self, item_id: str) -> RawIssue:
        return RawIssue(id=item_id, title="Issue", body=self._body, url="u", source="github")

    async def list_issues(self, *, filters=None, limit=20):
        return []

    async def post_comment(self, item_id, body):
        return "url"


class PMStub:
    name = "pm"

    async def run(self, state):
        return {"pm": {"note": "pm-ran"}}


class Noop:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        return {}


class PMPublishNoop:
    name = "pm"

    async def run(self, state):
        return {}


def _build(body: str):
    agents = {
        "intake": IntakeAgent(Settings(), provider=FakeProvider(body)),
        "pm": PMStub(),
        "pm_publish": PMPublishNoop(),
        "rfc": Noop("rfc"),
        "research": Noop("research"),
        "coding": Noop("coding"),
        "reviewer": Noop("reviewer"),
        "fixer": Noop("fixer"),
    }
    return build_graph(agents, checkpointer=MemorySaver())


def _as_dict(result):
    return WorkflowState.model_validate(result).model_dump()


async def _run(graph, mode, body, tid):
    initial = WorkflowState(run_id=tid, project="plane", item_id="1", intake_mode=mode)
    return _as_dict(await graph.ainvoke(initial, config={"configurable": {"thread_id": tid}}))


async def test_raw_to_spec_runs_pm():
    out = await _run(_build("raw text"), "raw_to_spec", "raw text", "t1")
    assert out["pm"]["note"] == "pm-ran"
    assert out["status"] == "completed"


async def test_raw_to_dev_skips_pm():
    out = await _run(_build("raw text"), "raw_to_dev", "raw text", "t2")
    assert out["pm"]["note"] is None  # PM never ran
    assert out["pm"]["spec"] is None


async def test_intake_error_skips_pm_and_fails_run():
    """When intake errors the graph routes straight to merge without running PM."""

    class BrokenProvider:
        kind = "github"

        async def fetch_issue(self, item_id: str) -> RawIssue:
            raise ValueError("no issue source configured")

        async def list_issues(self, *, filters=None, limit=20):
            return []

        async def post_comment(self, item_id, body):
            return "url"

    reached: list[str] = []

    class SpyPM:
        name = "pm"

        async def run(self, state):
            reached.append("pm")
            return {"pm": {"note": "pm-ran"}}

    agents = {
        "intake": IntakeAgent(Settings(), provider=BrokenProvider()),
        "pm": SpyPM(),
        "pm_publish": PMPublishNoop(),
        "rfc": Noop("rfc"),
        "research": Noop("research"),
        "coding": Noop("coding"),
        "reviewer": Noop("reviewer"),
        "fixer": Noop("fixer"),
    }
    graph = build_graph(agents, checkpointer=MemorySaver())
    initial = WorkflowState(run_id="t-err", project="plane", item_id="1")
    result = await graph.ainvoke(initial, config={"configurable": {"thread_id": "t-err"}})
    out = _as_dict(result)
    assert out["status"] == "failed"
    assert out["intake"]["error"] is not None
    assert "pm" not in reached  # PM must not run when intake failed


async def test_spec_ready_routes_through_pm():
    """spec_ready now routes through PM (different prompt, same nodes as raw_to_spec)."""
    out = await _run(
        _build("This is a pre-written spec document."), "spec_ready", "spec text", "t3"
    )
    # PMStub returns note="pm-ran" — confirms PM was reached
    assert out["pm"]["note"] == "pm-ran"
    assert out["status"] == "completed"
