"""Tests for PM workbench standalone routing + feedback loop (decision #29)."""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from ash.graph.builder import _route_after_pm_publish, _route_after_rfc, build_graph
from ash.graph.runner import Runner
from ash.graph.state import WorkflowState
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType


def _spec(tickets=None) -> Spec:
    return Spec(
        epic=Epic(title="t", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=tickets or [],
    )


def _state(**pm_kw) -> WorkflowState:
    st = WorkflowState(run_id="r", project="p", item_id="1", **{
        k: v for k, v in pm_kw.items() if k in ("pm_only", "story_mode")
    })
    if "next_action" in pm_kw:
        st.pm.next_action = pm_kw["next_action"]
    return st


# ── pure router functions ────────────────────────────────────────────────────


def test_route_after_pm_publish_full_run_continues_to_rfc():
    assert _route_after_pm_publish(_state(pm_only=False)) == "rfc"
    # next_action is ignored for full runs.
    assert _route_after_pm_publish(_state(pm_only=False, next_action="build")) == "rfc"


def test_route_after_pm_publish_workbench_default_stops():
    assert _route_after_pm_publish(_state(pm_only=True)) == "merge"


def test_route_after_pm_publish_workbench_rfc_and_build():
    assert _route_after_pm_publish(_state(pm_only=True, next_action="rfc")) == "rfc"
    assert _route_after_pm_publish(_state(pm_only=True, next_action="build")) == "plan_stories"


def test_route_after_rfc():
    assert _route_after_rfc(_state(pm_only=False)) == "plan_stories"
    assert _route_after_rfc(_state(pm_only=True, next_action="rfc")) == "merge"
    # workbench build path still plans stories after (defensive — rfc not the chosen action).
    assert _route_after_rfc(_state(pm_only=True, next_action="build")) == "plan_stories"


# ── integration: a pm_only run stops after the spec, a full run continues ─────


class SpecPM:
    name = "pm"

    def __init__(self) -> None:
        self.calls = 0
        self.last_feedback: str | None = None

    async def run(self, state: WorkflowState):
        self.calls += 1
        self.last_feedback = state.pm.feedback
        spec = _spec([Ticket(id="T1", title="t", description="d", type=TicketType.feature)])
        # Mirror the real PMAgent carry-forward: clear consumed feedback, keep the iteration count.
        return {
            "pm": {
                "spec": spec,
                "feedback": None,
                "regeneration_count": state.pm.regeneration_count,
                "ticket_feedback": dict(state.pm.ticket_feedback),
            }
        }


class GatePMPublish:
    """Real review gate — interrupts, then routes on the resume decision's `next`."""

    name = "pm"

    async def run(self, state: WorkflowState):
        raw = interrupt("spec_review")
        nxt = raw.get("next", "") if isinstance(raw, dict) else ""
        action = nxt if nxt in ("rfc", "build") else ""
        return {"pm": {"spec": state.pm.spec, "next_action": action}}


class Noop:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        return {}


def _runner(pm=None):
    agents = {n: Noop(n) for n in ("intake", "rfc", "research", "dev", "reviewer", "fixer")}
    agents["pm"] = pm or SpecPM()
    agents["pm_publish"] = GatePMPublish()
    return Runner(graph=build_graph(agents, checkpointer=MemorySaver()), pm_agent=agents["pm"])


async def test_pm_only_run_pauses_at_review_and_does_not_build():
    runner = _runner()
    run_id = await runner.start_run(project="plane", item_id="1", pm_only=True, wait=True)
    state = await runner.get_run(run_id)
    assert state["pending_review"] is True  # paused at the spec gate
    assert state["stories"] == {}  # never planned/built
    assert state["pm"]["spec"]["tickets"][0]["id"] == "T1"


async def test_pm_only_default_stops_at_merge_on_approve():
    runner = _runner()
    run_id = await runner.start_run(project="plane", item_id="1", pm_only=True, wait=True)
    # Approve with no follow-up → workbench stops (merge → END), no build.
    state = await runner.resume_run(run_id, {"action": "approve", "next": ""})
    assert state["status"] == "completed"
    assert state["stories"] == {}


async def test_regenerate_spec_threads_feedback_and_increments_iteration():
    pm = SpecPM()
    runner = _runner(pm)
    run_id = await runner.start_run(project="plane", item_id="1", pm_only=True, wait=True)
    assert pm.calls == 1

    state = await runner.regenerate_spec(run_id, feedback="split T1 in two", wait=True)
    assert pm.calls == 2
    assert pm.last_feedback == "split T1 in two"  # feedback reached the PM prompt
    assert state["pending_review"] is True  # re-paused at the gate
    assert state["pm"]["regeneration_count"] == 1
    assert state["pm"]["feedback"] is None  # consumed + cleared


async def test_refine_ticket_edits_one_ticket_and_keeps_interrupt():
    class RefinePM(SpecPM):
        async def refine_ticket(self, spec, ticket_id, feedback):
            t = next(t for t in spec.tickets if t.id == ticket_id)
            t = t.model_copy(deep=True)
            t.description = f"REFINED: {feedback}"
            return t

    pm = RefinePM()
    runner = _runner(pm)
    run_id = await runner.start_run(project="plane", item_id="1", pm_only=True, wait=True)

    state = await runner.refine_ticket(run_id, ticket_id="T1", feedback="tighten scope")
    assert state["pm"]["spec"]["tickets"][0]["description"] == "REFINED: tighten scope"
    assert state["pm"]["ticket_feedback"]["T1"] == "tighten scope"
    # The run must still be paused at the review gate (refine does not advance the graph).
    assert state["pending_review"] is True
