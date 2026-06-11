"""Intake agent — resolves the issue source and pulls a normalized `RawIssue`.

The front of the graph. It builds the `IssueProvider` from the selected integration (or a legacy
GitHub fallback derived from the project config), fetches the raw issue, and records it on the root
state. For `spec_ready` runs it parses the issue body as a `Spec` so PM can be skipped.
"""

from __future__ import annotations

from typing import Any

from ash.agents.base import BaseAgent
from ash.config.settings import Settings, load_project
from ash.graph.state import WorkflowState
from ash.integrations.base import IssueProvider
from ash.integrations.github import GitHubIssueProvider
from ash.integrations.service import provider_for
from ash.schemas import Spec


class IntakeAgent(BaseAgent):
    name = "intake"

    def __init__(self, settings: Settings, *, provider: IssueProvider | None = None) -> None:
        super().__init__(settings)
        self._provider = provider

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        provider = self._provider or await self._resolve(state)
        raw = await provider.fetch_issue(state.item_id)
        update: dict[str, Any] = {
            "raw_issue": raw,
            "issue_title": raw.title,
            "issue_url": raw.url,
            "intake": {"note": f"fetched from {raw.source or 'source'}"},
        }
        if state.intake_mode == "spec_ready":
            spec = Spec.model_validate_json(raw.body)
            update["pm"] = {"spec": spec, "note": "spec provided by source (spec_ready)"}
        return update

    async def _resolve(self, state: WorkflowState) -> IssueProvider:
        if state.integration_id is not None:
            return await provider_for(state.integration_id)
        # legacy fallback: derive a GitHub provider from the project's source repo
        project = load_project(state.project)
        return GitHubIssueProvider(
            token=self.settings.github_token,
            config={"repo": project.issues.source_repo},
        )
