"""Issue-source integrations (GitHub / Jira / Plane) behind one `IssueProvider` interface."""

from ash.integrations.base import IssueProvider, RawIssue
from ash.integrations.registry import build_provider
from ash.integrations.service import (
    create_integration,
    get_integration,
    list_integrations,
    provider_for,
)

__all__ = [
    "IssueProvider",
    "RawIssue",
    "build_provider",
    "create_integration",
    "get_integration",
    "list_integrations",
    "provider_for",
]
