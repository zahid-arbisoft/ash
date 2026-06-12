"""Intake agent — resolves the issue source and pulls a normalized `RawIssue`.

The front of the graph. It builds the `IssueProvider` from the selected integration (or a legacy
GitHub fallback derived from the project config), fetches the raw issue, and records it on the root
state.

Intake mode determines what happens next (conditional edge in the graph):
- raw_to_spec  → PM generates a full spec + tickets from the raw content, then the build team runs.
- spec_ready   → PM receives the content as a pre-written spec and extracts tickets (stories) from
                 it; no spec generation from scratch.
- raw_to_dev   → PM is skipped; the build team works directly from the raw issue content.

Intake itself only fetches and normalises the issue — routing is the graph's responsibility.
"""

from __future__ import annotations

from typing import Any

from ash.agents.base import BaseAgent
from ash.config.settings import Settings, load_project
from ash.graph.state import WorkflowState
from ash.integrations.base import IssueProvider
from ash.integrations.github import GitHubIssueProvider
from ash.integrations.service import provider_for


class IntakeAgent(BaseAgent):
    name = "intake"

    def __init__(self, settings: Settings, *, provider: IssueProvider | None = None) -> None:
        super().__init__(settings)
        self._provider = provider

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        # attachments-only run (no issue to fetch): PM works from the uploaded files
        if not state.item_id or state.item_id in {"upload", "-"}:
            return {"intake": {"note": "attachments-only run (no issue fetched)"}}

        provider = self._provider or await self._resolve(state)
        raw = await provider.fetch_issue(state.item_id)
        return {
            "raw_issue": raw,
            "issue_title": raw.title,
            "issue_url": raw.url,
            "intake": {"note": f"fetched from {raw.source or 'source'}"},
        }

    async def _resolve(self, state: WorkflowState) -> IssueProvider:
        if state.integration_id is not None:
            return await provider_for(state.integration_id)
        # Legacy fallback: build a GitHub provider from the project's issues config.
        # The preferred path is to add a GitHub connector at /admin and select it on the run form.
        project = load_project(state.project)
        if project.issues is None or not project.issues.source_repo.strip():
            raise ValueError(
                f"No issue source configured for project '{state.project}'. "
                "Fix one of these:\n"
                "  A) Add a GitHub/Jira/Plane connector at /admin and select it in the run form.\n"
                "  B) Set issues.source_repo in projects/"
                f"{state.project}.yaml (e.g. 'owner/repo').\n"
                "  C) Leave the item ID blank and upload spec files instead (attachments-only run)."
            )
        if not self.settings.github_token:
            raise ValueError(
                "GITHUB_TOKEN is not set in .env. "
                "Add it to use the project YAML's source repo directly, "
                "or add a GitHub connector at /admin with your token configured there."
            )
        return GitHubIssueProvider(
            token=self.settings.github_token,
            config={"repo": project.issues.source_repo},
        )
