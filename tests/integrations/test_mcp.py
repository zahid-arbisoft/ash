"""MCP connector loader — hosted-HTTP tool loading (servers mocked; no live MCP here)."""

from langchain_core.tools import StructuredTool

from ash.db.models import Connector, ConnectorKind
from ash.integrations import mcp


def _conn(**kw) -> Connector:
    defaults: dict = {
        "name": "gh-mcp",
        "kind": ConnectorKind.github,
        "transport": "http",
        "base_url": "https://api.githubcopilot.com/mcp/",
        "secret": "ghp_token",
        "config": {},
    }
    defaults.update(kw)
    return Connector(**defaults)


def test_is_mcp_only_for_http_transport():
    assert mcp.is_mcp(_conn()) is True
    assert mcp.is_mcp(Connector(name="x", kind=ConnectorKind.github, transport=None)) is False


def test_server_config_builds_streamable_http_with_bearer_auth():
    cfg = mcp.server_config(_conn())
    assert cfg["transport"] == "streamable_http"
    assert cfg["url"] == "https://api.githubcopilot.com/mcp/"
    assert cfg["headers"]["Authorization"] == "Bearer ghp_token"


def test_server_config_respects_explicit_headers():
    c = _conn(secret="", config={"headers": {"X-Api-Key": "abc"}})
    cfg = mcp.server_config(c)
    assert cfg["headers"] == {"X-Api-Key": "abc"}


def test_server_config_requires_base_url():
    import pytest

    c = Connector(name="bad", kind=ConnectorKind.github, transport="http", base_url=None)
    with pytest.raises(ValueError):
        mcp.server_config(c)


async def test_load_mcp_tools_returns_server_tools(monkeypatch):
    tool = StructuredTool.from_function(lambda x: x, name="get_issue", description="read an issue")

    class FakeClient:
        def __init__(self, connections):
            self.connections = connections

        async def get_tools(self):
            return [tool]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient, raising=True
    )
    tools = await mcp.load_mcp_tools(_conn())
    assert [t.name for t in tools] == ["get_issue"]


async def test_agent_can_call_a_loaded_mcp_tool():
    """The create_agent runtime can actually invoke an MCP-style tool (proves the wiring)."""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    calls: list[str] = []

    def get_issue(item_id: str) -> str:
        calls.append(item_id)
        return f"Issue {item_id}: title/body"

    mcp_tool = StructuredTool.from_function(
        get_issue, name="get_issue", description="read an issue"
    )

    class FakeModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    # turn 1: call the MCP tool; turn 2: final answer
    msgs = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_issue", "args": {"item_id": "42"}, "id": "1", "type": "tool_call"}
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    agent = create_agent(model=FakeModel(messages=msgs), tools=[mcp_tool])
    out = await agent.ainvoke({"messages": [("user", "read issue 42")]})
    assert calls == ["42"]
    assert out["messages"][-1].content == "done"
