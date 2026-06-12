from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ash.agents.pm import PMAgent
from ash.config.settings import Settings
from ash.graph.state import WorkflowState
from ash.integrations.base import RawIssue
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType
from ash.sinks.base import TicketRef


def _spec(tickets=None) -> Spec:
    return Spec(
        epic=Epic(
            title="CSV export",
            summary="Add CSV export",
            business_goal="users want it",
            acceptance_criteria=["exports csv"],
        ),
        technical_spec=TechnicalSpec(approach="add endpoint", testing_strategy="unit"),
        tickets=tickets or [],
    )


class FakeModel(GenericFakeChatModel):
    """create_agent-compatible fake: emits the structured-output tool call for `result`."""

    def __init__(self, result: BaseModel) -> None:
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": type(result).__name__,
                    "args": result.model_dump(),
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )
        super().__init__(messages=iter([msg]))

    def bind_tools(self, tools, **kwargs):  # create_agent binds the structured-output tool
        return self


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSink:
    kind = "fake"

    async def publish(self, spec):
        return [TicketRef(id=t.id, url=f"fake://{t.id}", sink=self.kind) for t in spec.tickets]


def _patch_sink(monkeypatch):
    # avoid any DB: fake the sessionmaker + the resolver
    monkeypatch.setattr("ash.agents.pm.get_sessionmaker", lambda: lambda: _FakeSession())

    async def _resolve(session, *, sink_id, board_dir):
        return _FakeSink()

    monkeypatch.setattr("ash.agents.pm.resolve_task_sink", _resolve)


async def test_pm_generates_spec_publishes_board_and_pushes_tickets(monkeypatch):
    spec = _spec(
        tickets=[
            Ticket(id="T1", title="Endpoint", description="add", type=TicketType.feature),
            Ticket(
                id="T2",
                title="Investigate format",
                description="unclear",
                type=TicketType.spike,
                needs_research=True,
            ),
        ]
    )
    published = {}

    class FakeBoard:
        def publish_spec(self, item_id, url, s):
            published.update(item_id=item_id, url=url, spec=s)
            return "board-ref-1"

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())
    _patch_sink(monkeypatch)

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="Add export", body="CSV please", source="github")

    update = await agent.run(state)

    assert update["pm"]["spec"] == spec
    assert update["pm"]["board_ref"] == "board-ref-1"
    assert update["pm"]["ticket_refs"] == ["fake://T1", "fake://T2"]
    assert "spikes for research: T2" in update["pm"]["note"]
    assert published["item_id"] == "42"


async def test_pm_keeps_spec_when_ticket_push_fails(monkeypatch):
    spec = _spec(tickets=[Ticket(id="T1", title="x", description="y", type=TicketType.feature)])
    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: _Board())
    monkeypatch.setattr("ash.agents.pm.get_sessionmaker", lambda: lambda: _FakeSession())

    class _BrokenSink:
        kind = "jira"

        async def publish(self, spec):
            raise RuntimeError("Jira 400 creating issue: bad issuetype")

    async def _resolve(session, *, sink_id, board_dir):
        return _BrokenSink()

    monkeypatch.setattr("ash.agents.pm.resolve_task_sink", _resolve)

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="t", body="b", source="github")

    update = await agent.run(state)
    # spec is preserved; the push failure is reported, not fatal
    assert update["pm"]["spec"] == spec
    assert "error" not in update["pm"]
    assert "ticket push failed" in update["pm"]["note"]


async def test_pm_uses_provided_spec_for_spec_ready(monkeypatch):
    spec = _spec()
    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: _Board())
    _patch_sink(monkeypatch)

    # no model needed: spec already present (spec_ready intake set it)
    agent = PMAgent(Settings())
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.pm.spec = spec

    update = await agent.run(state)
    assert update["pm"]["spec"] == spec


class _Board:
    def publish_spec(self, item_id, url, s):
        return "ref"
