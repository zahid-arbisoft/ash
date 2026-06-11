from ash.graph.state import WorkflowState


def test_default_substates_present():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    assert state.pm.spec is None
    assert state.research.plan is None
    assert state.coding.pr_url is None
    assert state.reviewer.note is None
    assert state.status == "running"


def test_substates_are_isolated():
    state = WorkflowState(run_id="r1", project="plane", item_id="42")
    state.pm.board_ref = "ref"
    assert state.research.plan is None  # writing pm must not touch research
