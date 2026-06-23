"""Research / Spike agent (read-only) — grounds a plan in the actual repo, in a git worktree.

Sets up the per-ticket worktree (parallel-safety primitive), then produces a grounded
`ImplementationPlan` via a tool-calling loop. Works from the PM spec when one exists, or
directly from the raw issue (`raw_to_dev`). No writes happen here.
With no local clone configured it records a skip note so a PM-only run still completes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ash.agents.base import BaseAgent, GuardrailBlockedError
from ash.agents.research_doc import publish_research_doc, render_research_doc
from ash.agents.worktree import ensure_worktree
from ash.config.settings import load_project
from ash.graph.state import WorkflowState
from ash.schemas import ImplementationPlan
from ash.toolkits.codebase import CodebaseToolkit

logger = logging.getLogger(__name__)

_SYSTEM = """You are a senior engineer doing a research spike. Given only a work brief, \
explore the codebase autonomously using the available tools — then produce a concrete, grounded \
implementation plan.

Exploration approach (like a code review session):
1. Call list_directory("", depth=2) to orient yourself on the repo structure.
2. Use search_codebase or grep_code to find files relevant to the brief.
3. Call read_file on the key files to understand existing patterns and conventions.
4. Repeat until you have enough grounding to write a precise plan.

Rules:
- Reference ONLY actual files/paths you discovered via tools.
- Prefer the smallest change that satisfies the brief.
- List open questions instead of guessing.
- Do NOT invent file names or APIs that you have not seen in the repo."""


class ResearchAgent(BaseAgent):
    name = "research"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        self._reset_usage()
        # Idempotency: if research already produced a plan and the user hasn't explicitly asked
        # for a re-run (no feedback), skip so retrigger-dev / retrigger-reviewer don't re-run
        # research from scratch (the story subgraph always starts at research — decision #33 fix).
        if state.research.plan and not state.research.feedback:
            logger.info("[research] plan already exists, passing through (retrigger of later step)")
            return {}
        skip = await self._trigger_gate(state)
        if skip is not None:
            return skip

        brief = state.brief(max_chars=self.settings.brief_max_chars)
        if not brief:
            logger.warning("[research] skipped: brief is empty (no spec or raw issue)")
            return {"research": {"note": "skipped: no spec or raw issue to work from"}}

        project = load_project(state.project)
        setup = await ensure_worktree(project, state, github_token=self.settings.github_token)
        if setup is None:
            logger.warning("[research] skipped: no local clone — set work.local_repo_path / LOCAL_REPO_PATH for project %r", state.project)
            return {
                "research": {
                    "note": "skipped: no local clone available "
                    "(configure work.local_repo_path / LOCAL_REPO_PATH for this project)"
                }
            }
        wt_path, branch = setup

        # Per-agent HITL feedback + optional custom prompt (decision #33), consumed once.
        extra = "\n\n".join(
            p for p in (
                (state.research.feedback or "").strip(),
                self._extra_instructions(state),
            ) if p
        )
        plan = await self._plan(wt_path, brief, extra=extra)
        logger.info("[research] plan complete steps=%d", len(plan.steps))

        doc_ref = await self._publish_doc(state, project, plan)

        return {
            "research": {
                "plan": plan,
                "branch": branch,
                "worktree_path": str(wt_path),
                "doc_ref": doc_ref,
                "tokens": dict(self._usage),
                "feedback": None,  # consumed this pass
            }
        }

    async def _publish_doc(
        self, state: WorkflowState, project: Any, plan: ImplementationPlan
    ) -> str | None:
        """Render the plan to Markdown, publish per `research_sink` (best-effort)."""
        doc = render_research_doc(plan, title=state.issue_title or state.item_id)
        try:
            return await publish_research_doc(
                mode=project.research_sink,
                runtime_dir=project.runtime_dir,
                run_id=state.run_id,
                doc=doc,
                integration_id=state.integration_id,
                item_id=state.item_id,
            )
        except Exception as exc:  # noqa: BLE001 — publishing is best-effort, never fail the run
            logger.warning("[research] doc publish failed (%s: %s)", type(exc).__name__, exc)
            return None

    async def _plan(self, worktree: Path, brief: str, *, extra: str = "") -> ImplementationPlan:
        toolkit = CodebaseToolkit(root=worktree)
        extra_section = f"\n\n## Additional instructions\n{extra}" if extra else ""
        user_msg = (
            f"## Work brief\n{brief}{extra_section}\n\n"
            "Use the tools to explore the codebase, then produce the implementation plan."
        )
        try:
            return await self.generate(
                ImplementationPlan, system=_SYSTEM, user=user_msg, tools=toolkit.get_tools(),
                context=brief,
            )
        except GuardrailBlockedError:
            logger.warning("[research] guardrail blocked — retrying without codebase tools")
            return await self.generate(
                ImplementationPlan,
                system=_SYSTEM,
                user=(
                    f"## Work brief\n{brief}\n\n"
                    "Note: codebase tools are unavailable. Produce a best-effort plan "
                    "from the brief alone."
                ),
                tools=[],
            )
