"""Map an `Integration` row to a concrete `IssueProvider`."""

from __future__ import annotations

from ash.db.models import Integration, ProviderKind
from ash.integrations.base import IssueProvider
from ash.integrations.github import GitHubIssueProvider
from ash.integrations.jira import JiraIssueProvider
from ash.integrations.plane import PlaneIssueProvider


def build_provider(integration: Integration) -> IssueProvider:
    config = integration.config or {}
    if integration.kind == ProviderKind.github:
        return GitHubIssueProvider(
            token=integration.secret, config=config, base_url=integration.base_url
        )
    if integration.kind == ProviderKind.jira:
        return JiraIssueProvider(
            token=integration.secret, config=config, base_url=integration.base_url
        )
    if integration.kind == ProviderKind.plane:
        return PlaneIssueProvider(
            token=integration.secret, config=config, base_url=integration.base_url
        )
    raise ValueError(f"Unknown integration kind: {integration.kind!r}")
