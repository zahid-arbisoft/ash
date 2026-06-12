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


async def test_pm_reads_markdown_spec_file_and_converts(monkeypatch, tmp_path):
    spec = _spec()

    class FakeBoard:
        def publish_spec(self, item_id, url, s):
            return "board-ref-2"

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())

    spec_file = tmp_path / "test.md"
    spec_file.write_text("# My Spec\n\nThis is a spec.\n")

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(
        run_id="r2",
        project="",
        item_id="",
        intake_mode="spec_file",
        spec_file_path=str(spec_file),
    )

    update = await agent.run(state)

    assert update["pm"]["spec"] is spec
    assert update["pm"]["ticket_refs"] == []


async def test_pm_creates_tickets_via_integration_when_integration_id_set(
    monkeypatch, tmp_path
):
    from ash.schemas import Ticket, TicketType

    ticket = Ticket(
        id="T1", title="Do work", description="Do the work", type=TicketType.feature
    )
    spec = _spec()
    spec.tickets = [ticket]

    created: list[tuple[str, str]] = []

    class FakeBoard:
        def publish_spec(self, item_id, url, s):
            return "board-ref"

    class FakeProvider:
        async def create_issue(self, title: str, body: str) -> str:
            created.append((title, body))
            return f"https://tracker/issues/{len(created)}"

    async def fake_provider_for(_id: int) -> FakeProvider:
        return FakeProvider()

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())
    monkeypatch.setattr("ash.agents.pm.provider_for", fake_provider_for)

    spec_file = tmp_path / "test.md"
    spec_file.write_text("# Spec\n")

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(
        run_id="r3",
        project="",
        item_id="",
        intake_mode="spec_file",
        spec_file_path=str(spec_file),
        integration_id=1,
    )

    update = await agent.run(state)

    assert update["pm"]["ticket_refs"] == ["https://tracker/issues/1"]
    assert created[0][0] == "Do work"


async def test_pm_generates_spec_from_raw_issue(monkeypatch):
    spec = _spec()

    class FakeBoard:
        def publish_spec(self, item_id, url, s):
            return "board-ref-1"

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="Add export", body="CSV please", source="github")

    update = await agent.run(state)

    assert update["pm"]["spec"] is spec
    assert update["pm"]["ticket_refs"] == []
