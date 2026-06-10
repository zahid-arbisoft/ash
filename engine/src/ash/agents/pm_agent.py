"""PM Agent — turns a single issue into a structured spec.

This is the highest-leverage agent: weak specs break everything downstream (the key insight in
agent_architecture.md). Phase 0 exists to validate this agent's output quality before we build any
orchestration around it.
"""

from __future__ import annotations

from ..connectors.github import Issue
from ..llm import LLMClient, StructuredResult
from ..schemas import Spec

_SYSTEM = """You are a senior product/technical lead acting as a Spec Builder for a software \
project. You receive a single GitHub issue and produce a rigorous, implementable specification.

Your spec must:
- Restate the problem and the desired outcome in clear language (don't just echo the issue).
- Define concrete, testable acceptance criteria.
- Surface edge cases the issue author likely didn't consider.
- Propose a sound technical approach and name the areas of the codebase likely affected.
- Break the work into small, independently shippable tickets with explicit dependencies.
- Assess risks honestly with severity and mitigations.

Be specific and grounded. Prefer the smallest change that fully satisfies the issue. If the issue \
is ambiguous, state your assumptions explicitly in the epic summary rather than inventing scope."""

_USER_TEMPLATE = """Produce a specification for the following issue.

Repository: {repo}
Issue #{number}: {title}
Labels: {labels}

--- Issue body ---
{body}
--- end body ---"""


def build_spec(llm: LLMClient, repo: str, issue: Issue) -> StructuredResult:
    user = _USER_TEMPLATE.format(
        repo=repo,
        number=issue.number,
        title=issue.title,
        labels=", ".join(issue.labels) or "(none)",
        body=issue.body.strip() or "(no description provided)",
    )
    return llm.generate_structured(role="pm", system=_SYSTEM, user=user, schema=Spec)
