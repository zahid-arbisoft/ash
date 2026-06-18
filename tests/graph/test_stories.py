"""Tests for per-story planning & sequencing (decision #26)."""

from ash.graph.state import RAW_STORY_ID, StoryState, WorkflowState
from ash.graph.stories import build_stories, next_story, topo_order
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType


def _ticket(tid, deps=None):
    return Ticket(
        id=tid, title=f"t{tid}", description="d", type=TicketType.feature, dependencies=deps or []
    )


def _spec(tickets):
    return Spec(
        epic=Epic(title="E", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=tickets,
    )


def _state(**kw):
    return WorkflowState(run_id="r", project="p", item_id="1", **kw)


def test_single_mode_builds_one_story():
    state = _state(story_mode="single")
    state.pm.spec = _spec([_ticket("T1"), _ticket("T2"), _ticket("T3")])
    stories, order = build_stories(state)
    assert list(stories) == ["T1"]
    assert order == ["T1"]


def test_multiple_mode_builds_all_stories():
    state = _state(story_mode="multiple")
    state.pm.spec = _spec([_ticket("T1"), _ticket("T2")])
    stories, order = build_stories(state)
    assert set(stories) == {"T1", "T2"}


def test_single_mode_ignores_selection():
    # single mode always produces one story regardless of story_selection so users
    # can't accidentally create multiple PRs by checking boxes in the review gate.
    state = _state(story_mode="single")
    state.pm.spec = _spec([_ticket("T1"), _ticket("T2"), _ticket("T3")])
    state.pm.story_selection = ["T2", "T3"]
    stories, _ = build_stories(state)
    assert list(stories) == ["T1"]


def test_selection_in_multiple_mode():
    # in multiple mode story_selection is honoured
    state = _state(story_mode="multiple")
    state.pm.spec = _spec([_ticket("T1"), _ticket("T2"), _ticket("T3")])
    state.pm.story_selection = ["T2", "T3"]
    stories, _ = build_stories(state)
    assert set(stories) == {"T2", "T3"}


def test_single_mode_ignores_stale_prior_stories():
    # decision #29: a stale `stories` dict from an earlier multi-story run must not leak extra
    # stories into single mode — exactly one story comes out.
    state = _state(story_mode="single")
    state.pm.spec = _spec([_ticket("T1"), _ticket("T2"), _ticket("T3")])
    state.stories = {
        "T1": StoryState(ticket_id="T1", status="completed"),
        "T2": StoryState(ticket_id="T2", status="completed"),
        "T3": StoryState(ticket_id="T3", status="completed"),
    }
    stories, order = build_stories(state)
    assert list(stories) == ["T1"]
    assert order == ["T1"]


def test_pm_only_build_forces_single_story():
    # decision #29: the workbench "build first story" action forces one story even in multiple mode.
    state = _state(story_mode="multiple", pm_only=True)
    state.pm.spec = _spec([_ticket("T1"), _ticket("T2"), _ticket("T3")])
    state.pm.next_action = "build"
    stories, _ = build_stories(state)
    assert list(stories) == ["T1"]


def test_no_spec_yields_one_synthetic_story():
    state = _state(issue_title="raw thing")
    stories, order = build_stories(state)
    assert order == [RAW_STORY_ID]
    assert stories[RAW_STORY_ID].title == "raw thing"


def test_topo_order_respects_dependencies():
    stories = {
        "T1": StoryState(ticket_id="T1", deps=["T2"]),
        "T2": StoryState(ticket_id="T2", deps=[]),
        "T3": StoryState(ticket_id="T3", deps=["T1"]),
    }
    order = topo_order(stories)
    assert order.index("T2") < order.index("T1") < order.index("T3")


def test_topo_order_cycle_falls_back_to_insertion():
    stories = {
        "A": StoryState(ticket_id="A", deps=["B"]),
        "B": StoryState(ticket_id="B", deps=["A"]),
    }
    assert topo_order(stories) == ["A", "B"]  # no crash, stable fallback


def test_next_story_skips_dep_blocked_until_dep_done():
    state = _state()
    state.stories = {
        "T1": StoryState(ticket_id="T1", deps=[], status="pending"),
        "T2": StoryState(ticket_id="T2", deps=["T1"], status="pending"),
    }
    state.story_order = ["T1", "T2"]
    assert next_story(state) == "T1"  # T2 is blocked on T1

    state.stories["T1"].status = "completed"
    assert next_story(state) == "T2"  # now unblocked

    state.stories["T2"].status = "completed"
    assert next_story(state) is None  # all done
