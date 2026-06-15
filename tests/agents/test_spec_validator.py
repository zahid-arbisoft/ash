from ash.agents.spec_validator import validate_spec
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType


def _spec(tickets) -> Spec:
    return Spec(
        epic=Epic(title="t", summary="s", business_goal="b", acceptance_criteria=["a"]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=tickets,
    )


def _t(tid, *, deps=None, type=TicketType.feature, needs_research=False) -> Ticket:
    return Ticket(
        id=tid,
        title=f"title {tid}",
        description=f"description for {tid}",
        type=type,
        needs_research=needs_research,
        dependencies=deps or [],
    )


def test_valid_spec_has_no_errors():
    spec = _spec([_t("T1"), _t("T2", deps=["T1"]), _t("T3", deps=["T1", "T2"])])
    assert validate_spec(spec) == []


def test_detects_circular_dependency():
    spec = _spec([_t("T2", deps=["T3"]), _t("T3", deps=["T2"])])
    errors = validate_spec(spec)
    assert any("Circular dependency" in e for e in errors)


def test_detects_self_dependency():
    spec = _spec([_t("T1", deps=["T1"])])
    errors = validate_spec(spec)
    assert any("depends on itself" in e for e in errors)


def test_detects_dangling_dependency():
    spec = _spec([_t("T1", deps=["T9"])])
    errors = validate_spec(spec)
    assert any("not a ticket id" in e for e in errors)


def test_detects_duplicate_ids():
    spec = _spec([_t("T1"), _t("T1")])
    errors = validate_spec(spec)
    assert any("Duplicate ticket id" in e for e in errors)


def test_detects_spike_without_research_flag():
    spec = _spec([_t("T1", type=TicketType.spike, needs_research=False)])
    errors = validate_spec(spec)
    assert any("needs_research" in e for e in errors)


def test_longer_cycle_is_caught():
    spec = _spec([_t("T1", deps=["T2"]), _t("T2", deps=["T3"]), _t("T3", deps=["T1"])])
    errors = validate_spec(spec)
    assert any("Circular dependency" in e for e in errors)


def test_empty_spec_is_valid():
    assert validate_spec(_spec([])) == []
