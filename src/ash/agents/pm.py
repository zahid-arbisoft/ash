"""PM agent (v2) — requirements → spec → (human review) → tickets pushed to the task tool.

Two-phase design:
- **PMAgent** (`pm` node): ingest requirements, generate Spec, publish to the Board for oversight.
- **PMPublishAgent** (`pm_publish` node): interrupt for human review; on approval push tickets to
  the selected sink (Plane / Jira / file board).

Splitting into two nodes means the spec is checkpointed before the HITL gate, so the LLM call is
never repeated on resume.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from ash.agents.base import BaseAgent
from ash.clients.board import get_board
from ash.config.settings import load_project
from ash.db.base import get_sessionmaker
from ash.documents import read_documents
from ash.graph.state import WorkflowState
from ash.schemas import Spec
from ash.sinks.service import resolve_task_sink

# ── raw_to_spec: PM builds a full spec + breaks it into implementation tickets ──

_SYSTEM_RAW_TO_SPEC = """\
You are a senior product/technical lead acting as a Spec Builder. You receive raw requirements \
(issue text and/or uploaded documents) and produce a rigorous, implementable specification.

Your spec must:
- Restate the problem and desired outcome in clear language (don't just echo the input).
- Define concrete, testable acceptance criteria.
- Surface edge cases the author likely didn't consider.
- Propose a sound technical approach and name the areas of the codebase likely affected.
- Break the work into small, independently shippable implementation tickets (stories).
- Mark any ticket that needs investigation before implementation as a SPIKE: set its type to \
"spike" and `needs_research` to true (the Research agent will pick those up).
- Assess risks honestly with severity and mitigations.

Be specific and grounded. Prefer the smallest change that fully satisfies the requirements.\
"""

_USER_RAW_TO_SPEC = """\
Produce a specification and implementation tickets from the following raw requirements.

{context}

--- end requirements ---\
"""

# ── spec_ready: PM receives a pre-written spec and extracts implementation tickets ──

_SYSTEM_SPEC_READY = """\
You are a senior PM. You receive a pre-written specification document. The spec already describes \
what needs to be built — your job is NOT to create new requirements, but to:
1. Parse and structure the spec into the standard output format (epic summary, technical approach).
2. Break the specified work into small, independently shippable implementation tickets (stories) \
   that a developer can pick up and execute without ambiguity.
3. Mark any ticket that needs investigation or research first as a SPIKE (`needs_research = true`).
4. Identify risks and mitigations that are evident from the spec.

Stay faithful to the provided spec. Do not invent features or requirements not mentioned.\
"""

_USER_SPEC_READY = """\
The following is a pre-written specification. Structure it and extract implementation tickets.

{context}

--- end specification ---\
"""


class PMAgent(BaseAgent):
    """Phase 1: ingest requirements, generate Spec, write to the Board. No ticket push yet."""

    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        project = load_project(state.project)

        context = await self._gather(state)
        if not context.strip():
            return {"pm": {"error": "no requirements: provide an issue or attachments"}}

        if state.intake_mode == "spec_ready":
            system, user = _SYSTEM_SPEC_READY, _USER_SPEC_READY
        else:  # raw_to_spec (raw_to_dev never reaches PM)
            system, user = _SYSTEM_RAW_TO_SPEC, _USER_RAW_TO_SPEC

        spec = await self.generate(Spec, system=system, user=user.format(context=context))

        item_id = state.item_id or "upload"
        board = get_board(project.runtime_dir / "board")
        board_ref = await asyncio.to_thread(board.publish_spec, item_id, state.issue_url, spec)

        spikes = [t.id for t in spec.tickets if t.needs_research]
        mode_label = "Spec extracted" if state.intake_mode == "spec_ready" else "Spec generated"
        note = f"{mode_label} ({len(spec.tickets)} ticket(s)) — awaiting your review"
        if spikes:
            note += f"; spikes: {', '.join(spikes)}"

        return {
            "pm": {
                "spec": spec,
                "board_ref": board_ref,
                "note": note,
            }
        }

    async def _gather(self, state: WorkflowState) -> str:
        parts: list[str] = []
        if state.raw_issue is not None:
            parts.append(f"# {state.raw_issue.title}\n\n{state.raw_issue.body}")
        if state.attachments:
            parts.append(await asyncio.to_thread(read_documents, state.attachments))
        return "\n\n".join(p for p in parts if p.strip())


class PMPublishAgent(BaseAgent):
    """Phase 2: interrupt for human review; push tickets to the connector on approval."""

    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        spec = state.pm.spec
        if spec is None:
            # PM failed or produced nothing — don't interrupt the user, just propagate the failure.
            return {}

        # Pause — LangGraph checkpoints here; the UI shows Approve / Reject.
        # `interrupt()` returns the value passed to Command(resume=...) on the resume call.
        decision: str = interrupt("spec_review")

        if decision == "reject":
            return {
                "pm": {
                    "spec": spec,
                    "board_ref": state.pm.board_ref,
                    "ticket_refs": [],
                    "note": "ticket push cancelled by reviewer",
                }
            }

        project = load_project(state.project)
        board_dir = project.runtime_dir / "board"
        spikes = [t.id for t in spec.tickets if t.needs_research]
        try:
            async with get_sessionmaker()() as session:
                sink = await resolve_task_sink(
                    session, sink_id=state.task_sink_id, board_dir=board_dir
                )
            refs = await sink.publish(spec)
            note = f"{len(refs)} ticket(s) pushed"
            ticket_refs = [r.url or r.id for r in refs]
        except Exception as exc:  # noqa: BLE001 — keep spec; report push failure
            note = f"spec approved, but ticket push failed: {type(exc).__name__}: {exc}"
            ticket_refs = []
        if spikes:
            note += f"; spikes for research: {', '.join(spikes)}"

        return {
            "pm": {
                "spec": spec,
                "board_ref": state.pm.board_ref,
                "ticket_refs": ticket_refs,
                "note": note,
            }
        }
