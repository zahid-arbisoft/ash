"""Deterministic spec validation — structural checks the LLM can't reliably self-enforce.

Prompts ask the model to produce a sound spec; this module *proves* the parts that are decidable
in code (dependency graph integrity, spike/research consistency). The PM agent runs this after
generation and feeds any errors back for one self-correction round, so structurally broken specs
(circular dependencies, dangling ticket references) never reach the human review gate silently.
"""

from __future__ import annotations

from ash.schemas import Spec, TicketType


def validate_spec(spec: Spec) -> list[str]:
    """Return a list of human-readable structural problems. Empty list = the spec is sound."""
    errors: list[str] = []
    ids = [t.id for t in spec.tickets]
    id_set = set(ids)

    # Duplicate ticket ids make dependency references ambiguous.
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"Duplicate ticket id {dup!r} — ids must be unique.")

    # Dangling / self dependencies.
    for t in spec.tickets:
        for dep in t.dependencies:
            if dep == t.id:
                errors.append(f"Ticket {t.id} depends on itself.")
            elif dep not in id_set:
                errors.append(
                    f"Ticket {t.id} depends on {dep!r}, which is not a ticket id in this spec."
                )

    # Circular dependencies — a developer could never pick a starting ticket.
    cycle = _find_cycle(spec)
    if cycle:
        errors.append("Circular dependency detected: " + " -> ".join(cycle))

    # Spike / research-flag consistency (the Research agent keys off both).
    for t in spec.tickets:
        if t.type == TicketType.spike and not t.needs_research:
            errors.append(
                f"Ticket {t.id} is type 'spike' but needs_research is false — spikes are handed "
                "to the Research agent, so set needs_research=true."
            )

    return errors


def _find_cycle(spec: Spec) -> list[str] | None:
    """Return one dependency cycle as an id path (e.g. ['T2','T3','T2']), or None if acyclic."""
    id_set = {t.id for t in spec.tickets}
    # edge ticket -> dependency (the dependency must complete first); ignore dangling refs here.
    graph: dict[str, list[str]] = {
        t.id: [d for d in t.dependencies if d in id_set] for t in spec.tickets
    }
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in graph[node]:
            if color[nxt] == GRAY:  # back-edge into the current path = cycle
                return stack[stack.index(nxt) :] + [nxt]
            if color[nxt] == WHITE:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for tid in graph:
        if color[tid] == WHITE:
            found = dfs(tid)
            if found:
                return found
    return None
