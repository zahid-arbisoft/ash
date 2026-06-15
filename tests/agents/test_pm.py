from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ash.agents.pm import PMAgent, PMPublishAgent
from ash.config.settings import Settings
from ash.graph.state import PMState, WorkflowState
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
    monkeypatch.setattr("ash.agents.pm.get_sessionmaker", lambda: lambda: _FakeSession())

    async def _resolve(session, *, sink_id, board_dir):
        return _FakeSink()

    monkeypatch.setattr("ash.agents.pm.resolve_task_sink", _resolve)


class _Board:
    def publish_spec(self, item_id, url, s):
        return "board-ref-1"


# ── PMAgent (phase 1: spec generation + board write) ─────────────────────────


async def test_pm_generates_spec_and_writes_board(monkeypatch):
    """PMAgent generates the spec and writes to the board; ticket push
    is deferred to PMPublishAgent."""
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
    published: dict = {}

    class FakeBoard:
        def publish_spec(self, item_id, url, s):
            published.update(item_id=item_id, url=url, spec=s)
            return "board-ref-1"

    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: FakeBoard())

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.raw_issue = RawIssue(id="42", title="Add export", body="CSV please", source="github")

    update = await agent.run(state)

    assert update["pm"]["spec"] == spec
    assert update["pm"]["board_ref"] == "board-ref-1"
    assert "ticket_refs" not in update["pm"]  # tickets pushed in phase 2
    assert "awaiting your review" in update["pm"]["note"]
    assert "spikes: T2" in update["pm"]["note"]
    assert published["item_id"] == "42"


async def test_pm_spec_ready_uses_extract_prompt(monkeypatch):
    """spec_ready mode: PM generates spec from content (different note label, same output shape)."""
    spec = _spec()
    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: _Board())

    agent = PMAgent(Settings(), model=FakeModel(spec))
    state = WorkflowState(run_id="r1", project="plane", item_id="42", intake_mode="spec_ready")
    state.raw_issue = RawIssue(id="42", title="Spec doc", body="Pre-written spec.", source="github")

    update = await agent.run(state)
    assert update["pm"]["spec"] == spec
    assert "Spec extracted" in update["pm"]["note"]


# ── PMPublishAgent (phase 2: HITL interrupt + ticket push) ───────────────────


def _state_with_spec(spec: Spec, task_sink_id=None) -> WorkflowState:
    state = WorkflowState(run_id="r1", project="plane", item_id="42", task_sink_id=task_sink_id)
    state.pm = PMState(spec=spec, board_ref="board-ref-1")
    return state


async def test_pm_publish_pushes_tickets_on_approve(monkeypatch):
    spec = _spec(
        tickets=[
            Ticket(id="T1", title="Endpoint", description="add", type=TicketType.feature),
            Ticket(
                id="T2",
                title="Investigate",
                description="unclear",
                type=TicketType.spike,
                needs_research=True,
            ),
        ]
    )
    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: _Board())
    _patch_sink(monkeypatch)
    monkeypatch.setattr("ash.agents.pm.interrupt", lambda _value: "approve")

    agent = PMPublishAgent(Settings())
    update = await agent.run(_state_with_spec(spec))

    assert update["pm"]["spec"] == spec
    assert update["pm"]["board_ref"] == "board-ref-1"
    assert update["pm"]["ticket_refs"] == ["fake://T1", "fake://T2"]
    assert "2 ticket(s) pushed" in update["pm"]["note"]
    assert "spikes for research: T2" in update["pm"]["note"]


async def test_pm_publish_cancels_on_reject(monkeypatch):
    spec = _spec(tickets=[Ticket(id="T1", title="x", description="y", type=TicketType.feature)])
    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: _Board())
    monkeypatch.setattr("ash.agents.pm.interrupt", lambda _value: "reject")

    agent = PMPublishAgent(Settings())
    update = await agent.run(_state_with_spec(spec))

    assert update["pm"]["spec"] == spec
    assert update["pm"]["ticket_refs"] == []
    assert "cancelled" in update["pm"]["note"]


async def test_pm_publish_keeps_spec_when_push_fails(monkeypatch):
    spec = _spec(tickets=[Ticket(id="T1", title="x", description="y", type=TicketType.feature)])
    monkeypatch.setattr("ash.agents.pm.get_board", lambda _dir: _Board())
    monkeypatch.setattr("ash.agents.pm.get_sessionmaker", lambda: lambda: _FakeSession())
    monkeypatch.setattr("ash.agents.pm.interrupt", lambda _value: "approve")

    class _BrokenSink:
        kind = "jira"

        async def publish(self, spec):
            raise RuntimeError("Jira 400 creating issue: bad issuetype")

    async def _resolve(session, *, sink_id, board_dir):
        return _BrokenSink()

    monkeypatch.setattr("ash.agents.pm.resolve_task_sink", _resolve)

    agent = PMPublishAgent(Settings())
    update = await agent.run(_state_with_spec(spec))

    assert update["pm"]["spec"] == spec
    assert "error" not in update["pm"]
    assert "ticket push failed" in update["pm"]["note"]


