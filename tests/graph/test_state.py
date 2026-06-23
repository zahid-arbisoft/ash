from ash.graph.state import PMState, WorkflowState
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType


def _spec_with_tickets() -> Spec:
    return Spec(
        epic=Epic(title="Exports", summary="Add exports", business_goal="b",
                  acceptance_criteria=["a"]),
        technical_spec=TechnicalSpec(approach="x", testing_strategy="unit"),
        tickets=[
            Ticket(id="T1", title="CSV", description="Add CSV export endpoint.",
                   type=TicketType.feature, acceptance_criteria=["csv works"]),
            Ticket(id="T2", title="XLSX", description="Add XLSX export endpoint.",
                   type=TicketType.feature, dependencies=["T1"]),
        ],
    )


def test_brief_whole_spec_when_no_ticket():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.pm = PMState(spec=_spec_with_tickets())
    brief = state.brief()
    assert "T1" in brief and "T2" in brief  # full spec JSON


def test_brief_scoped_to_single_ticket():
    state = WorkflowState(run_id="r1", project="plane", item_id="42", ticket_id="T2")
    state.pm = PMState(spec=_spec_with_tickets())
    brief = state.brief()
    assert "Ticket T2: XLSX" in brief
    assert "Add XLSX export endpoint." in brief
    assert "Depends on: T1" in brief
    assert "Add CSV export endpoint." not in brief  # other tickets excluded


def test_brief_unknown_ticket_falls_back_to_full_spec():
    state = WorkflowState(run_id="r1", project="plane", item_id="42", ticket_id="T99")
    state.pm = PMState(spec=_spec_with_tickets())
    brief = state.brief()
    assert "T1" in brief and "T2" in brief  # unknown id → whole spec


def test_default_substates_present():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    assert state.pm.spec is None
    assert state.research.plan is None
    assert state.dev.pr_url is None
    assert state.reviewer.note is None
    assert state.status == "running"


def test_substates_are_isolated():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.pm.board_ref = "ref"
    assert state.research.plan is None  # writing pm must not touch research
