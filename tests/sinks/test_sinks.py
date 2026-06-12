import httpx

from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType
from ash.sinks.file import FileBoardSink
from ash.sinks.jira import JiraTaskSink
from ash.sinks.plane import PlaneTaskSink


def _spec() -> Spec:
    return Spec(
        epic=Epic(title="E", summary="s", business_goal="b", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=[
            Ticket(id="T1", title="Do it", description="desc", type=TicketType.feature),
            Ticket(
                id="T2",
                title="Spike it",
                description="unclear",
                type=TicketType.spike,
                needs_research=True,
            ),
        ],
    )


async def test_file_sink_writes_tickets(tmp_path):
    refs = await FileBoardSink(tmp_path).publish(_spec())
    assert [r.id for r in refs] == ["T1", "T2"]
    assert (tmp_path / "ticket-T1.md").exists()
    spike_md = (tmp_path / "ticket-T2.md").read_text()
    assert "SPIKE" in spike_md


async def test_jira_sink_creates_issues():
    created = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue"
        created.append(request)
        return httpx.Response(201, json={"key": f"ENG-{len(created)}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sink = JiraTaskSink(
            token="t",
            config={"email": "e@x.com", "project_key": "ENG"},
            base_url="https://x.atlassian.net",
            http=http,
        )
        refs = await sink.publish(_spec())

    assert [r.id for r in refs] == ["ENG-1", "ENG-2"]
    assert refs[0].url == "https://x.atlassian.net/browse/ENG-1"
    assert len(created) == 2


async def test_plane_sink_creates_issues():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/issues/" in request.url.path
        return httpx.Response(201, json={"id": "abc", "url": "https://plane/abc"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sink = PlaneTaskSink(
            token="k",
            config={"workspace_slug": "w", "project_id": "p"},
            base_url="https://plane.example",
            http=http,
        )
        refs = await sink.publish(_spec())

    assert all(r.sink == "plane" for r in refs)
    assert refs[0].id == "abc"
