"""PM agent (real) — issue → structured spec → Board sink.

The highest-leverage agent: weak specs break everything downstream. It reads the board item via the
GitHub client, generates a rigorous `Spec` (structured output), and publishes it to the Board (specs
go to the Board, not the PR — plan §4c). Posting the spec back as an issue comment is a deferred
feature (the `BoardToolkit.post_board_comment` seam already exists).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx
from langchain_core.language_models import BaseChatModel

from ash.agents.base import BaseAgent
from ash.clients.board import get_board
from ash.clients.github import GitHubClient, Issue
from ash.config.settings import Settings, load_project
from ash.graph.state import WorkflowState
from ash.schemas import Spec

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

_USER = """Produce a specification for the following issue.

Repository: {repo}
Issue #{number}: {title}
Labels: {labels}

--- Issue body ---
{body}
--- end body ---"""


class _BoardClient(Protocol):
    async def get_issue(self, item_id: str | int) -> Issue: ...
    async def post_comment(self, item_id: str | int, body: str) -> str: ...


class PMAgent(BaseAgent):
    name = "pm"

    def __init__(
        self,
        settings: Settings,
        *,
        model: BaseChatModel | None = None,
        github: _BoardClient | None = None,
    ) -> None:
        super().__init__(settings, model=model)
        self._github = github

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        project = load_project(state.project)
        repo = project.issues.source_repo

        if self._github is not None:
            issue = await self._github.get_issue(state.item_id)
        else:
            async with httpx.AsyncClient(timeout=30) as http:
                gh = GitHubClient(token=self.settings.github_token, repo=repo, http=http)
                issue = await gh.get_issue(state.item_id)

        spec = await self._build_spec(repo, issue)

        board = get_board(project.runtime_dir / "board")
        board_ref = await asyncio.to_thread(board.publish_spec, issue.number, issue.url, spec)

        return {
            "pm": {"spec": spec, "board_ref": board_ref},
            "issue_title": issue.title,
            "issue_url": issue.url,
        }

    async def _build_spec(self, repo: str, issue: Issue) -> Spec:
        user = _USER.format(
            repo=repo,
            number=issue.number,
            title=issue.title,
            labels=", ".join(issue.labels) or "(none)",
            body=issue.body.strip() or "(no description provided)",
        )
        return await self.generate(Spec, system=_SYSTEM, user=user)
