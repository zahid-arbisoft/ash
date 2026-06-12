from pathlib import Path

import httpx
from fastapi import FastAPI

from ash.api.routes import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return app


def _read(path: str) -> str:
    return Path(path).read_text()


async def test_upload_files_returns_paths_and_writes_them():
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/uploads",
            files=[
                ("files", ("spec.md", b"# Title\n\nbody", "text/markdown")),
                ("files", ("notes.txt", b"plain", "text/plain")),
            ],
        )
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert len(paths) == 2
    assert _read(paths[0]) == "# Title\n\nbody"
