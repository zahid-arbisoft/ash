"""PM agent (real) — raw issue / spec file → structured spec → local record + ticket creation.

The highest-leverage agent: weak specs break everything downstream. It takes the `RawIssue` that
the intake node fetched (or a spec file), generates a rigorous `Spec` (structured output), writes
it to runtime/board/ for local reference, and creates tickets in the selected integration.
Posting the spec back as a comment is a deferred feature (the integration's `post_comment` seam
already exists).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ash.agents.base import BaseAgent
from ash.clients.board import get_board
from ash.config.settings import RUNTIME_DIR
from ash.graph.state import WorkflowState
from ash.integrations.base import IssueProvider, RawIssue
from ash.integrations.service import provider_for
from ash.schemas import Spec, Ticket
from ash.utils.file_extract import to_markdown

logger = logging.getLogger(__name__)

_SYSTEM = """You are a senior product/technical lead acting as a Spec Builder for a software \
project. You receive a single issue and produce a rigorous, implementable specification.

Your spec must:
- Restate the problem and the desired outcome in clear language (don't just echo the issue).
- Define concrete, testable acceptance criteria.
- Surface edge cases the issue author likely didn't consider.
- Propose a sound technical approach and name the areas of the codebase likely affected.
- Break the work into small, independently shippable tickets with explicit dependencies.
- Assess risks honestly with severity and mitigations.

Be specific and grounded. Prefer the smallest change that fully satisfies the issue. If the issue \
is ambiguous, state your assumptions explicitly in the epic summary rather than inventing scope."""

_USER = """Produce a specification for the following issue.

Source: {source}
Item {item_id}: {title}
Labels: {labels}

--- Issue body ---
{body}
--- end body ---"""

_SPEC_FILE_SYSTEM = """You are a senior technical lead. You receive a Markdown spec document \
and extract it into a rigorous, structured specification.

Faithfully represent the document — do not add scope, invent requirements, or change intent. \
If information for a field is missing, state your interpretation in the relevant field."""

_SPEC_FILE_USER = """Extract this Markdown spec document into structured output.

--- spec document ---
{content}
--- end document ---"""


def _ticket_body(ticket: Ticket) -> str:
    lines = [ticket.description, ""]
    if ticket.acceptance_criteria:
        lines += ["## Acceptance Criteria"]
        lines += [f"- [ ] {c}" for c in ticket.acceptance_criteria]
        lines += [""]
    if ticket.dependencies:
        lines += [f"**Dependencies:** {', '.join(ticket.dependencies)}", ""]
    if ticket.estimate:
        lines += [f"**Estimate:** {ticket.estimate}", ""]
    lines += [f"**Type:** {ticket.type.value}"]
    return "\n".join(lines).strip()


def _read_spec_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")
    return to_markdown(path)



class PMAgent(BaseAgent):
    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        if state.intake_mode == "spec_file":
            if state.spec_file_path is None:
                return {"pm": {"error": "spec_file mode but no spec_file_path on state"}}
            try:
                spec = await self._build_spec_from_file(state.spec_file_path)
            except FileNotFoundError as exc:
                return {"pm": {"error": str(exc)}}
        else:
            raw = state.raw_issue
            if raw is None:
                return {"pm": {"error": "no raw issue from intake"}}
            spec = await self._build_spec(raw)

        await asyncio.to_thread(
            get_board(RUNTIME_DIR / "board").publish_spec, state.item_id, state.issue_url, spec
        )

        ticket_refs: list[str] = []
        if state.integration_id is not None:
            logger.info(
                "run_id=%s creating %d ticket(s) in integration_id=%s",
                state.run_id, len(spec.tickets), state.integration_id,
            )
            provider = await provider_for(state.integration_id)
            ticket_refs = await self._create_tickets(provider, spec.tickets)
            logger.info("run_id=%s tickets created: %s", state.run_id, ticket_refs)

        return {"pm": {"spec": spec, "ticket_refs": ticket_refs}}

    async def _create_tickets(self, provider: IssueProvider, tickets: list[Ticket]) -> list[str]:
        refs = []
        for ticket in tickets:
            ref = await provider.create_issue(ticket.title, _ticket_body(ticket))
            refs.append(ref)
        return refs

    async def _build_spec_from_file(self, spec_file_path: str) -> Spec:
        abs_path = Path(spec_file_path)
        content = await asyncio.to_thread(_read_spec_file, abs_path)
        user = _SPEC_FILE_USER.format(content=content)
        return await self.generate(Spec, system=_SPEC_FILE_SYSTEM, user=user)

    async def _build_spec(self, raw: RawIssue) -> Spec:
        user = _USER.format(
            source=raw.source or "unknown",
            item_id=raw.id,
            title=raw.title,
            labels=", ".join(raw.labels) or "(none)",
            body=raw.body.strip() or "(no description provided)",
        )
        return await self.generate(Spec, system=_SYSTEM, user=user)
