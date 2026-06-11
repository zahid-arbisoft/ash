import httpx

from ash.integrations.github import GitHubIssueProvider
from ash.integrations.jira import JiraIssueProvider, _adf_to_text
from ash.integrations.plane import PlaneIssueProvider


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_github_provider_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(
            200, json={"number": 42, "title": "Bug", "body": "broke", "html_url": "u"}
        )

    async with _client(handler) as http:
        p = GitHubIssueProvider(token="tok", config={"repo": "o/r"}, http=http)
        issue = await p.fetch_issue("42")
    assert issue.id == "42"
    assert issue.title == "Bug"
    assert issue.source == "github"


async def test_jira_provider_fetch_flattens_adf():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/rest/api/3/issue/ENG-1" in str(request.url)
        return httpx.Response(
            200,
            json={
                "key": "ENG-1",
                "fields": {
                    "summary": "Add export",
                    "description": {
                        "type": "doc",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Need CSV"}]}
                        ],
                    },
                    "labels": ["backend"],
                    "status": {"statusCategory": {"key": "indeterminate"}},
                },
            },
        )

    async with _client(handler) as http:
        p = JiraIssueProvider(
            token="t",
            config={"email": "e@x.com", "project_key": "ENG"},
            base_url="https://x.atlassian.net",
            http=http,
        )
        issue = await p.fetch_issue("ENG-1")
    assert issue.id == "ENG-1"
    assert issue.title == "Add export"
    assert "Need CSV" in issue.body
    assert issue.labels == ["backend"]
    assert issue.url == "https://x.atlassian.net/browse/ENG-1"


def test_adf_to_text_plain_string():
    assert _adf_to_text("hello") == "hello"
    assert _adf_to_text(None) == ""


async def test_plane_provider_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "abc",
                "name": "Title",
                "description_stripped": "body text",
                "state": "open",
            },
        )

    async with _client(handler) as http:
        p = PlaneIssueProvider(
            token="k",
            config={"workspace_slug": "w", "project_id": "p"},
            base_url="https://plane.example",
            http=http,
        )
        issue = await p.fetch_issue("abc")
    assert issue.id == "abc"
    assert issue.title == "Title"
    assert issue.body == "body text"
    assert issue.source == "plane"
