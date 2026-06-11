from ash.agents.pm import PMAgent
from ash.clients.github import Issue
from ash.config.settings import Settings
from ash.graph.state import WorkflowState
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


class FakeGitHub:
    async def get_issue(self, item_id):
        return Issue(number=int(item_id), title="Add export", body="CSV please", url="u")

    async def post_comment(self, item_id, body):
        return "https://gh/comment/7"


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


async def test_pm_reads_issue_generates_spec_publishes_board(monkeypatch):
    spec = _spec()
    published = {}

    class FakeBoard:
        def publish_spec(self, number, url, s):
            published.update(number=number, url=url, spec=s)
            return "board-ref-1"

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())

    agent = PMAgent(Settings(), model=FakeModel(spec), github=FakeGitHub())
    state = WorkflowState(run_id="r1", project="plane", item_id="42")

    update = await agent.run(state)

    assert update["pm"]["spec"] is spec
    assert update["pm"]["board_ref"] == "board-ref-1"
    assert update["issue_title"] == "Add export"
    assert published["number"] == 42
