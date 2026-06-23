"""Reviewer agent — the checker half of maker/checker (plan §4a, §10.4).

Reviews the change the Coding agent produced **in depth, in a single pass** (to avoid review
cycles), tags every finding by severity, and renders a verdict. When a PR exists it posts the review
via `gh`; when the project's autonomy policy allows auto-merge *and* the change is approved with no
blocking findings, it merges — otherwise the merge waits for a human (ApprovalGate).

This is a different agent instance from Coding (separate prompt/model allowed), never the writer.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from ash.agents.base import BaseAgent
from ash.clients import pr as pr_client
from ash.config.settings import load_project
from ash.gates import ApprovalGate
from ash.graph.state import WorkflowState
from ash.schemas import CodeChange, CodeReview, ReviewVerdict

_SYSTEM = """You are a meticulous senior code reviewer performing a SINGLE, in-depth review pass. \
You are NOT the author — be appropriately skeptical, but fair.

You are given the work brief/spec and the full content of every file the author created or \
modified. Review for: correctness and bugs, security, adherence to the spec's acceptance criteria, \
missing or inadequate tests, error handling, and clear style issues. Review thoroughly in ONE \
pass — do not defer to a later cycle; surface everything you can find now.

For each issue, produce a finding with:
- the file path and (if you can determine it) the 1-based line number,
- a severity: `critical` (must fix — breaks/insecure), `high` (must fix — likely bug/spec miss), \
`medium` (should fix), `low` (minor), or `nit` (nice-to-have/style),
- a short category (bug / security / tests / style / spec), and
- a clear comment explaining what is wrong and why, plus a concrete suggestion when possible.

Then set the verdict:
- `approve` only if the change is correct, complete against the spec, and has no high/critical \
issues.
- `request_changes` if any high/critical issue exists or the change is incomplete.

Be concise and specific. An empty findings list with `approve` means the change is clean."""


class ReviewerAgent(BaseAgent):
    name = "reviewer"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        self._reset_usage()
        skip = await self._trigger_gate(state)
        if skip is not None:
            return skip

        change = state.coding.change
        if change is None or not change.edits:
            return {"reviewer": {"note": "skipped: no code change to review"}}

        review = await self._review(state.brief(max_chars=self.settings.brief_max_chars), change)
        verdict = review.verdict.value
        blocking = review.blocking()

        project = load_project(state.project)
        gate = ApprovalGate(project.autonomy)
        pr_url = state.coding.pr_url

        comment_url = await self._post_review(pr_url, review) if pr_url else None

        merged = False
        notes: list[str] = [
            f"{verdict} — {len(review.findings)} finding(s)"
            + (f", {len(blocking)} blocking" if blocking else "")
        ]
        if review.verdict is ReviewVerdict.approve and not blocking:
            if project.autonomy.auto_merge_on_approve:
                if gate.requires_human("merge"):
                    # P4b: interrupt for human merge approval (resume with "approve" to merge).
                    decision: Any = interrupt(
                        {
                            "reason": "merge_approval",
                            "pr_url": pr_url,
                            "verdict": verdict,
                            "findings": len(review.findings),
                        }
                    )
                    if decision == "approve":
                        merged = await self._merge(pr_url)
                        notes.append("merged after human approval")
                    else:
                        notes.append(f"merge declined (decision={decision})")
                else:
                    merged = await self._merge(pr_url)
                    notes.append(
                        "auto-merged" if merged else "auto-merge policy on (no PR to merge)"
                    )
            else:
                notes.append("approved — awaiting human merge")
        else:
            notes.append("changes requested — Fixer will address findings")

        from ash.observability import langsmith as _ls
        _ls.score(
            state.run_id,
            "reviewer_verdict",
            1.0 if review.verdict is ReviewVerdict.approve else 0.0,
            comment=f"{verdict} | findings={len(review.findings)} blocking={len(blocking)}"
            + (f" | ticket={state.current_story}" if state.current_story else ""),
        )
        return {
            "reviewer": {
                "review": review,
                "verdict": verdict,
                "comment_url": comment_url,
                "merged": merged,
                "note": "; ".join(notes),
                "tokens": dict(self._usage),
            }
        }

    async def _review(self, brief: str, change: CodeChange) -> CodeReview:
        total_cap = self.settings.files_max_chars
        # Per-file cap is the smaller of a fixed 4000 and an even split of the total budget,
        # so a change touching many files stays within the total (bounds small-context models).
        per_file = min(4_000, max(1, total_cap // len(change.edits))) if change.edits else 4_000
        snippets = []
        for e in change.edits:
            body = e.content or ""
            truncated = len(body) > per_file
            excerpt = body[:per_file] + ("\n… (truncated)" if truncated else "")
            snippets.append(f"### {e.path} ({e.action.value})\n```\n{excerpt}\n```")
        files = "\n\n".join(snippets)
        user = (
            f"## Work brief / spec\n{brief}\n\n"
            f"## Author's summary\n{change.summary}\n\n"
            f"## Author's test note\n{change.tests_note or '(none)'}\n\n"
            f"## Changed files\n{files}\n\n"
            "Review the change and return your findings and verdict."
        )
        return await self.generate(CodeReview, system=_SYSTEM, user=user)

    async def _post_review(self, pr_url: str | None, review: CodeReview) -> str | None:
        if not pr_url:
            return None
        body = _render_review(review)
        try:
            approve = review.verdict is ReviewVerdict.approve and not review.blocking()
            await asyncio.to_thread(pr_client.review_pr, pr=pr_url, body=body, approve=approve)
            return pr_url
        except Exception:  # noqa: BLE001 — posting is best-effort; review is still recorded
            return None

    async def _merge(self, pr_url: str | None) -> bool:
        if not pr_url:
            return False
        try:
            await asyncio.to_thread(pr_client.merge_pr, pr=pr_url)
            return True
        except Exception:  # noqa: BLE001 — merge failure shouldn't crash the run
            return False


def _render_review(review: CodeReview) -> str:
    """Render a CodeReview as a Markdown PR review body."""
    lines = [f"## ASH review — **{review.verdict.value}**", "", review.summary, ""]
    if review.findings:
        lines.append("### Findings")
        for f in review.findings:
            loc = f"`{f.path}`" + (f":{f.line}" if f.line else "")
            cat = f" _{f.category}_" if f.category else ""
            lines.append(f"- **[{f.severity.value}]**{cat} {loc} — {f.comment}")
            if f.suggestion:
                lines.append(f"  - _Suggestion:_ {f.suggestion}")
    else:
        lines.append("_No issues found._")
    lines.append("\n_Reviewed by the ASH Reviewer agent._")
    return "\n".join(lines)
