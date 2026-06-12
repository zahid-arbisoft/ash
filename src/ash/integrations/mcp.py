"""Load tools from a hosted (HTTP) MCP server described by a `Connector`.

When a connector's `transport == "http"`, we don't use a bespoke httpx client — we connect to the
system's own **MCP server** (GitHub / Atlassian / …) via `langchain-mcp-adapters` and hand its tools
(`get_issue`, `create_issue`, `create_pull_request`, …) to the agents. Auth is the connector's
encrypted `secret` (sent as a bearer token by default) plus any extra `config["headers"]`.

Only remote/hosted HTTP is wired here (no local stdio servers in the image).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from ash.db.models import Connector

MCP_TRANSPORT = "http"


def is_mcp(connector: Connector) -> bool:
    return (connector.transport or "").lower() == MCP_TRANSPORT


def server_config(connector: Connector) -> dict[str, Any]:
    """Build the langchain-mcp-adapters StreamableHttp connection for this connector."""
    if not connector.base_url:
        raise ValueError(f"MCP connector {connector.name!r} needs base_url (the MCP server URL)")
    headers: dict[str, str] = dict((connector.config or {}).get("headers", {}))
    if connector.secret and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {connector.secret}"
    return {"transport": "streamable_http", "url": connector.base_url, "headers": headers}


async def load_mcp_tools(connector: Connector) -> list[BaseTool]:
    """Connect to the connector's hosted MCP server and return its tools as LangChain tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({connector.name: server_config(connector)})
    return await client.get_tools()
