"""Story planning & sequencing (decision #26).

Pure functions that turn a run's spec (or raw issue) into a set of `StoryState`s and decide, at each
loop turn, which story to build next — honouring ticket dependencies. Kept side-effect-free so they
are trivially unit-testable; the graph nodes in `builder.py` call them.
"""

from __future__ import annotations

from ash.graph.state import RAW_STORY_ID, StoryState, WorkflowState


def selected_ticket_ids(state: WorkflowState) -> list[str] | None:
    """Tickets the human chose to build as stories at the review gate (F1), or None if not set.

    Stored on `pm.ticket_refs`? No — selection is a distinct concern; we read it from a dedicated
    field set by the publish gate. Returns None when no explicit selection was made (build all /
    single default applies).
    """
    sel = getattr(state.pm, "story_selection", None)
    if sel is None:
        return None
    return [t for t in sel if t]


def build_stories(state: WorkflowState) -> tuple[dict[str, StoryState], list[str]]:
    """Build the per-story map + dependency-sorted execution order for this run.

    - With a spec: one story per ticket, filtered by the human's selection (F1) or, absent a
      selection, by `story_mode` (single → the first implementable ticket; multiple → all).
    - Without a spec (raw_to_dev / empty spec): a single synthetic story (`RAW_STORY_ID`).
    Idempotent over existing state: a story already present (e.g. a completed one from a prior
    partial run) keeps its prior status/branch/pr_url.
    """
    spec = state.pm.spec
    existing = state.stories
    stories: dict[str, StoryState] = {}

    if spec is not None and spec.tickets:
        chosen = selected_ticket_ids(state)
        tickets = list(spec.tickets)
        if state.story_mode == "single":
            # Single mode always produces at most one story — the first non-spike
            # (or the first ticket overall if all are spikes). Human checkbox selections
            # are ignored in single mode so the user can't accidentally create multiple PRs
            # by checking boxes in the review gate.
            non_spike = [t for t in tickets if not t.needs_research]
            tickets = (non_spike[:1] if non_spike else tickets[:1])
        elif chosen is not None:
            tickets = [t for t in tickets if t.id in chosen]
        if not tickets:  # selection emptied everything → fall back to the first ticket
            tickets = list(spec.tickets)[:1]
        valid_ids = {t.id for t in tickets}
        for t in tickets:
            prior = existing.get(t.id)
            if prior is not None:
                stories[t.id] = prior
                continue
            stories[t.id] = StoryState(
                ticket_id=t.id,
                title=t.title,
                # only keep deps that are themselves part of the built set
                deps=[d for d in t.dependencies if d in valid_ids],
            )
    else:
        prior = existing.get(RAW_STORY_ID)
        stories[RAW_STORY_ID] = prior or StoryState(
            ticket_id=RAW_STORY_ID,
            title=state.issue_title or state.item_id or "change",
        )

    return stories, topo_order(stories)


def topo_order(stories: dict[str, StoryState]) -> list[str]:
    """Dependency-respecting order (Kahn). Falls back to insertion order if a cycle is detected,
    so a malformed dependency graph never deadlocks the run."""
    ids = list(stories.keys())
    indeg = dict.fromkeys(ids, 0)
    for i in ids:
        for d in stories[i].deps:
            if d in indeg:
                indeg[i] += 1
    queue = [i for i in ids if indeg[i] == 0]
    ordered: list[str] = []
    while queue:
        n = queue.pop(0)
        ordered.append(n)
        for i in ids:
            if n in stories[i].deps:
                indeg[i] -= 1
                if indeg[i] == 0:
                    queue.append(i)
    if len(ordered) != len(ids):  # cycle → stable fallback
        return ids
    return ordered


def next_story(state: WorkflowState) -> str | None:
    """The next `pending` story whose dependencies are all terminal (completed/skipped), walking
    `story_order`. Returns None when nothing is left to build."""
    order = state.story_order or list(state.stories.keys())
    for tid in order:
        story = state.stories.get(tid)
        if story is None or story.status != "pending":
            continue
        deps_done = all(
            (dep := state.stories.get(d)) is not None
            and dep.status in ("completed", "skipped")
            for d in story.deps
        )
        if deps_done:
            return tid
    # No pending story with satisfied deps. If pending stories remain but are dep-blocked
    # (e.g. a dep failed), treat as done so the run can terminate rather than spin.
    return None
