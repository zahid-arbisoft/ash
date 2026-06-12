"""Connectors used as issue sources (GitHub / Jira / Plane) behind one `IssueProvider` interface."""

from ash.integrations.base import IssueProvider, RawIssue
from ash.integrations.mcp import is_mcp, load_mcp_tools
from ash.integrations.registry import build_provider
from ash.integrations.service import (
    create_connector,
    get_connector,
    list_connectors,
    mcp_tools_for,
    provider_for,
)

__all__ = [
    "IssueProvider",
    "RawIssue",
    "build_provider",
    "create_connector",
    "get_connector",
    "is_mcp",
    "list_connectors",
    "load_mcp_tools",
    "mcp_tools_for",
    "provider_for",
]
