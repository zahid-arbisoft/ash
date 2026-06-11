"""PM agent (real) — raw issue → structured spec → Board sink.

The highest-leverage agent: weak specs break everything downstream. It takes the `RawIssue` that
the intake node fetched, generates a rigorous `Spec` (structured output), and publishes it to the
Board (specs go to the Board, not the PR — plan §4c). Posting the spec back as a comment is a
deferred feature (the integration's `post_comment` seam already exists).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ash.agents.base import BaseAgent
from ash.clients.board import get_board
from ash.config.settings import load_project
from ash.graph.state import WorkflowState
from ash.integrations.base import RawIssue
from ash.schemas import Spec

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


class PMAgent(BaseAgent):
    name = "pm"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        raw = state.raw_issue
        if raw is None:
            return {"pm": {"error": "no raw issue from intake"}}

        spec = await self._build_spec(raw)

        project = load_project(state.project)
        board = get_board(project.runtime_dir / "board")
        board_ref = await asyncio.to_thread(board.publish_spec, raw.id, raw.url, spec)

        return {"pm": {"spec": spec, "board_ref": board_ref}}

    async def _build_spec(self, raw: RawIssue) -> Spec:
        user = _USER.format(
            source=raw.source or "unknown",
            item_id=raw.id,
            title=raw.title,
            labels=", ".join(raw.labels) or "(none)",
            body=raw.body.strip() or "(no description provided)",
        )
        return await self.generate(Spec, system=_SYSTEM, user=user)
