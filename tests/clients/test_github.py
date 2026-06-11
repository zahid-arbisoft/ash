import httpx

from ash.clients.github import GitHubClient, Issue


async def test_get_issue_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(
            200, json={"number": 42, "title": "Bug", "body": "It broke", "html_url": "u"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = GitHubClient(token="tok", repo="o/r", http=http)
        issue = await client.get_issue("42")

    assert issue == Issue(number=42, title="Bug", body="It broke", url="u")


async def test_post_comment_returns_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"html_url": "https://gh/comment/1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = GitHubClient(token="tok", repo="o/r", http=http)
        url = await client.post_comment("42", "the spec")

    assert url == "https://gh/comment/1"


async def test_list_issues_filters_out_prs():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"number": 1, "title": "real", "body": ""},
                {"number": 2, "title": "pr", "body": "", "pull_request": {}},
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = GitHubClient(token="", repo="o/r", http=http)
        issues = await client.list_issues(limit=10)

    assert [i.number for i in issues] == [1]
