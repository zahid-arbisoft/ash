"""Map a `Connector` row to a concrete `IssueProvider` (for connectors used as a source)."""

from __future__ import annotations

from ash.db.models import Connector, ConnectorKind
from ash.integrations.base import IssueProvider
from ash.integrations.github import GitHubIssueProvider
from ash.integrations.jira import JiraIssueProvider
from ash.integrations.plane import PlaneIssueProvider


def build_provider(connector: Connector) -> IssueProvider:
    config = connector.config or {}
    if connector.kind == ConnectorKind.github:
        return GitHubIssueProvider(
            token=connector.secret, config=config, base_url=connector.base_url
        )
    if connector.kind == ConnectorKind.jira:
        return JiraIssueProvider(token=connector.secret, config=config, base_url=connector.base_url)
    if connector.kind == ConnectorKind.plane:
        return PlaneIssueProvider(
            token=connector.secret, config=config, base_url=connector.base_url
        )
    raise ValueError(f"connector kind {connector.kind.value!r} cannot be an issue source")
