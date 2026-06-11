from ash.agents.pm import PMAgent
from ash.config.settings import Settings
from ash.graph.state import WorkflowState
from ash.integrations.base import RawIssue
from ash.schemas import Epic, Spec, TechnicalSpec


def _spec() -> Spec:
    return Spec(
        epic=Epic(
            title="CSV export",
            summary="Add CSV export",
            business_goal="users want it",
            acceptance_criteria=["exports csv"],
        ),
        technical_spec=TechnicalSpec(approach="add endpoint", testing_strategy="unit"),
        tickets=[],
    )


class _Structured:
    def __init__(self, result):
        self._result = result

    async def ainvoke(self, messages):
        return self._result


class FakeModel:
    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return _Structured(self._result)


async def test_pm_generates_spec_from_raw_issue_and_publishes_board(monkeypatch):
    spec = _spec()
    published = {}

    class FakeBoard:
        def publish_spec(self, item_id, url, s):
            published.update(item_id=item_id, url=url, spec=s)
            return "board-ref-1"

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="Add export", body="CSV please", source="github")

    update = await agent.run(state)

    assert update["pm"]["spec"] is spec
    assert update["pm"]["board_ref"] == "board-ref-1"
    assert published["item_id"] == "42"
