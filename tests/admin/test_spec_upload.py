from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver
from sqladmin import Admin
from sqlalchemy.ext.asyncio import AsyncEngine

from ash.admin.spec_upload import SpecUploadView
from ash.graph.builder import build_graph
from ash.graph.runner import Runner


class StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, state):
        return {self.name: {"note": "ok"}} if self.name != "pm" else {"issue_title": "ok"}


def _app() -> FastAPI:
    app = FastAPI()
    engine = MagicMock(spec=AsyncEngine)
    templates_dir = str(Path(__file__).parent.parent.parent / "src/ash/admin/templates")
    admin = Admin(app, engine, title="ASH Admin", templates_dir=templates_dir)
    admin.add_base_view(SpecUploadView)
    agents = {n: StubAgent(n) for n in ("intake", "pm", "research", "coding", "reviewer", "fixer")}
    app.state.runner = Runner(graph=build_graph(agents, checkpointer=MemorySaver()))
    admin.admin.state.outer_app = app
    return app


async def test_get_upload_form_returns_html():
    with patch("ash.admin.spec_upload.list_integrations", AsyncMock(return_value=[])):
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/spec-upload")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "upload" in resp.text.lower()


async def test_post_upload_saves_file_and_redirects(tmp_path):
    md_content = b"# My Spec\n\nThis is a spec.\n"
    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("ash.admin.spec_upload.RUNTIME_DIR", tmp_path),
        patch("ash.admin.spec_upload.get_sessionmaker") as mock_sm,
        patch("ash.admin.spec_upload.list_integrations", AsyncMock(return_value=[])),
    ):
        mock_sm.return_value.return_value = mock_session_ctx

        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/spec-upload",
                files={"spec_file": ("my_spec.md", md_content, "text/markdown")},
                follow_redirects=False,
            )

    assert resp.status_code in (302, 303)
    saved = list((tmp_path / "uploads").rglob("*.md"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == md_content
    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()
