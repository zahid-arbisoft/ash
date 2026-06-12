"""Connectors used as issue sources (GitHub / Jira / Plane) behind one `IssueProvider` interface."""

from ash.integrations.base import IssueProvider, RawIssue
from ash.integrations.registry import build_provider
from ash.integrations.service import (
    create_connector,
    get_connector,
    list_connectors,
    provider_for,
)

__all__ = [
    "IssueProvider",
    "RawIssue",
    "build_provider",
    "create_connector",
    "get_connector",
    "list_connectors",
    "provider_for",
]
