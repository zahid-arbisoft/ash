"""PM agent (v2) — requirements → spec → tickets pushed to the user's task tool.

Responsibilities:
- **Ingest** requirements from the issue text *and/or* uploaded files (pdf/docx/md/… via the
  documents reader).
- **Spec:** use a provided spec (`spec_ready`) or generate one from the requirements; publish it to
  the Board for oversight.
- **Tickets:** break the spec into tickets and **push them to the selected task sink** (Plane / Jira
  / file board) — explicit per-run choice, else the admin-managed default, else the local board.
- **Spikes:** tickets PM marks `needs_research` are flagged so the Research agent can pick them up.

NOTE: PM's spec step is a single structured-output call (deterministic, easily tested). Converting
the looping agents (Research/Coding/Reviewer/Fixer) to `create_agent` is the next phase (see
docs/plan/agent_runtime_and_connectors_plan.md).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ash.agents.base import BaseAgent
from ash.clients.board import get_board
from ash.config.settings import load_project
from ash.db.base import get_sessionmaker
from ash.documents import read_documents
from ash.graph.state import WorkflowState
from ash.schemas import Spec
from ash.sinks.service import resolve_task_sink

_SYSTEM = """You are a senior product/technical lead acting as a Spec Builder. You receive raw \
requirements (issue text and/or uploaded documents) and produce a rigorous, implementable \
specification.

Your spec must:
- Restate the problem and desired outcome in clear language (don't just echo the input).
- Define concrete, testable acceptance criteria.
- Surface edge cases the author likely didn't consider.
- Propose a sound technical approach and name the areas of the codebase likely affected.
- Break the work into small, independently shippable tickets with explicit dependencies.
- Mark any ticket that needs investigation before implementation as a SPIKE: set its type to \
"spike" and `needs_research` to true (the Research agent will pick those up).
- Assess risks honestly with severity and mitigations.

Be specific and grounded. Prefer the smallest change that fully satisfies the requirements."""

_USER = """Produce a specification from the following requirements.

{context}

--- end requirements ---"""


class PMAgent(BaseAgent):
    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        project = load_project(state.project)

        spec = state.pm.spec  # already set for spec_ready intake
        if spec is None:
            context = await self._gather(state)
            if not context.strip():
                return {"pm": {"error": "no requirements: provide an issue or attachments"}}
            spec = await self.generate(Spec, system=_SYSTEM, user=_USER.format(context=context))

        item_id = state.item_id or "upload"
        board = get_board(project.runtime_dir / "board")
        board_ref = await asyncio.to_thread(board.publish_spec, item_id, state.issue_url, spec)

        # Pushing tickets is secondary: a sink failure must NOT discard the generated spec.
        spikes = [t.id for t in spec.tickets if t.needs_research]
        try:
            refs = await self._publish_tickets(spec, state, project.runtime_dir / "board")
            note = f"{len(refs)} ticket(s) pushed"
            ticket_refs = [r.url or r.id for r in refs]
        except Exception as exc:  # noqa: BLE001 — keep the spec; report the push failure
            note = f"spec ready, but ticket push failed: {type(exc).__name__}: {exc}"
            ticket_refs = []
        if spikes:
            note += f"; spikes for research: {', '.join(spikes)}"

        return {
            "pm": {
                "spec": spec,
                "board_ref": board_ref,
                "ticket_refs": ticket_refs,
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

    async def _publish_tickets(self, spec: Spec, state: WorkflowState, board_dir: Any) -> list[Any]:
        async with get_sessionmaker()() as session:
            sink = await resolve_task_sink(session, sink_id=state.task_sink_id, board_dir=board_dir)
        return await sink.publish(spec)
