"""Tests for deterministic estimate repair (decision #29)."""

import pytest

from ash.agents.estimates import (
    parse_estimate_days,
    repair_spec_estimates,
    repair_ticket_estimates,
)
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("S", 0.5),
        ("m", 2.0),
        ("L", 5.0),
        ("XS", 0.25),
        ("XL", 10.0),
        ("3d", 3.0),
        ("1w", 5.0),
        ("8h", 1.0),
        ("4h", 0.5),
        ("0.5d", 0.5),
        ("2", 2.0),  # bare number → days
        ("  3D ", 3.0),  # whitespace + case
        ("", None),
        (None, None),
        ("soon", None),  # unparseable
    ],
)
def test_parse_estimate_days(text, expected):
    assert parse_estimate_days(text) == expected


def _ticket(**kw) -> Ticket:
    base = {"id": "T1", "title": "t", "description": "d", "type": TicketType.feature}
    base.update(kw)
    return Ticket(**base)


def test_repair_fills_days_from_text():
    t = repair_ticket_estimates(_ticket(estimate="3d"), speedup=6.0)
    assert t.estimate_days == 3.0
    assert t.llm_estimate_days == 0.5  # 3 / 6
    assert t.llm_estimate  # text backfilled


def test_repair_derives_llm_when_missing():
    t = repair_ticket_estimates(_ticket(estimate="L", estimate_days=5.0), speedup=5.0)
    assert t.llm_estimate_days == 1.0  # 5 / 5


def test_repair_enforces_llm_strictly_smaller():
    # LLM emitted an llm_estimate_days >= traditional → repaired down via the speedup factor.
    t = repair_ticket_estimates(
        _ticket(estimate="2d", estimate_days=2.0, llm_estimate_days=3.0), speedup=6.0
    )
    assert t.llm_estimate_days < t.estimate_days
    assert t.llm_estimate_days == pytest.approx(2.0 / 6.0, abs=1e-3)


def test_repair_compact_all_zero_estimates():
    # The compact-mode failure mode: LLM left every estimate field blank/zero. Repair recovers
    # both numbers from the traditional text alone.
    t = repair_ticket_estimates(
        _ticket(estimate="1w", estimate_days=0.0, llm_estimate="", llm_estimate_days=0.0),
        speedup=6.0,
    )
    assert t.estimate_days == 5.0
    assert 0 < t.llm_estimate_days < 5.0
    assert t.estimate and t.llm_estimate


def test_repair_unparseable_text_leaves_days_none():
    # Nothing to parse and no days given → can't invent numbers; leaves them unset, no crash.
    t = repair_ticket_estimates(_ticket(estimate="dunno"), speedup=6.0)
    assert t.estimate_days is None
    assert t.llm_estimate_days is None


def test_repair_spec_applies_to_all_tickets():
    spec = Spec(
        epic=Epic(title="E", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=[_ticket(id="T1", estimate="3d"), _ticket(id="T2", estimate="S")],
    )
    out = repair_spec_estimates(spec, speedup=6.0)
    assert all(t.llm_estimate_days and t.llm_estimate_days < t.estimate_days for t in out.tickets)
