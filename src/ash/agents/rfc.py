"""RFC agent — generates a structured RFC document from requirements (plan §10.6).

Runs AFTER pm_publish and BEFORE research. Gated by the standard trigger policy (default
**manual**): the run pauses with a "Trigger RFC" control; the human triggers it to generate the
design doc, or skips it. Set `agents.rfc.trigger: auto` to generate one automatically every run,
or `enabled: false` to turn it off entirely.

The RFC agent takes the PM spec (or the raw issue for raw_to_dev runs) and
produces a formal RFC Markdown document (Background, Problem Statement,
Proposed Solution, Alternatives, Acceptance Criteria, Open Questions).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ash.agents.base import BaseAgent, GuardrailBlockedError
from ash.graph.state import WorkflowState
from ash.schemas import RFCDocument

logger = logging.getLogger(__name__)

_SYSTEM = """You are a senior technical architect writing a Request for Comments (RFC) document.

You are given the requirements or a PM spec for a planned change. Your job is to produce a
thorough, well-structured RFC that will guide the implementation team.

Guidelines:
- Be precise and concrete. Reference specific systems, APIs, or data structures where known.
- Background: explain WHY this is needed and what problem it solves for users.
- Problem Statement: define the exact problem with enough precision that any engineer can
  understand what "solved" means.
- Proposed Solution: describe HOW to solve it — architecture decisions, key trade-offs, impact
  on other systems. Enough detail for a senior engineer to start implementation.
- Alternatives: briefly explain what else was considered and why you chose this approach.
- Acceptance Criteria: concrete, testable conditions. At least 3 items.
- Open Questions: things that need answers before or during implementation."""


class RFCAgent(BaseAgent):
    name = "rfc"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        self._reset_usage()
        # RFC is opt-in: only runs when trigger=auto AND enabled. Resolve the policy
        # once (DB override > YAML > default) and reuse it for both the gate and the
        # opt-in check so they can never disagree.
        resolved = await self._resolve_policy(state)
        if resolved is None:
            return {"rfc": {"note": "skipped: could not load project/policy config"}}
        project, _policy = resolved

        # RFC follows the standard trigger gate, like every other agent:
        #   disabled        → skip
        #   trigger=manual  → interrupt and wait for a human Trigger (or Skip)  [default]
        #   trigger=auto    → run automatically
        # (Default is manual, so RFC no longer auto-runs every run; the human triggers it.)
        skip = await self._trigger_gate(state, resolved=resolved)
        if skip is not None:
            return skip

        brief = state.brief()
        if not brief:
            return {"rfc": {"note": "skipped: no brief to generate RFC from"}}

        logger.info("[rfc] generating RFC document")
        try:
            rfc_doc = await self._generate(brief)
        except GuardrailBlockedError as exc:
            # RFC is an opt-in, non-blocking design doc. If the LLM gateway's content
            # guardrail blocks it (e.g. a false-positive on the brief's wording), skip
            # gracefully so the run continues to research/build instead of failing.
            logger.warning("[rfc] guardrail blocked RFC generation — skipping: %s", exc)
            return {"rfc": {"note": f"skipped: RFC generation blocked by LLM guardrail ({exc})"}}
        md = rfc_doc.to_markdown()
        logger.info("[rfc] RFC generated: %s (%d chars)", rfc_doc.title, len(md))

        # Publish to disk so the client can actually find the RFC (mirrors research docs).
        doc_ref = await self._publish(project, state.run_id, md)
        note = f"RFC generated: {rfc_doc.title}"
        if doc_ref:
            note += f" → {doc_ref}"
        return {
            "rfc": {
                "doc": md,
                "doc_ref": doc_ref,
                "title": rfc_doc.title,
                "note": note,
                "tokens": dict(self._usage),
            }
        }

    async def _publish(self, project: Any, run_id: str, md: str) -> str | None:
        """Write the RFC Markdown to `<runtime_dir>/rfc/<run_id>.md`. Best-effort."""

        def _write() -> str:
            dest = project.runtime_dir / "rfc"
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"{run_id}.md"
            path.write_text(md)
            return str(path)

        try:
            return await asyncio.to_thread(_write)
        except Exception as exc:  # noqa: BLE001 — publishing is best-effort, never fail the run
            logger.warning("[rfc] doc publish failed (%s: %s)", type(exc).__name__, exc)
            return None

    async def _generate(self, brief: str) -> RFCDocument:
        user = (
            "## Requirements / Spec\n\n"
            + brief
            + "\n\nGenerate a complete RFC document based on the above."
        )
        return await self.generate(RFCDocument, system=_SYSTEM, user=user)
