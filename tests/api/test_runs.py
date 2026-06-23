import asyncio

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver

from ash.api.routes import router
from ash.graph.builder import build_graph
from ash.graph.runner import Runner


class StubAgent:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        if self.name == "pm":
            return {"issue_title": "ok"}
        if self.name == "pm_publish":
            # skip the HITL interrupt in tests — just return immediately
            return {"pm": {"note": "published (stub)"}}
        return {self.name: {"note": "ok"}}


def _app():
    app = FastAPI()
    app.include_router(router)
    agents = {
        n: StubAgent(n)
        for n in ("intake", "pm", "pm_publish", "rfc", "research", "dev", "reviewer", "fixer")
    }
    app.state.runner = Runner(graph=build_graph(agents, checkpointer=MemorySaver()))
    return app


async def test_post_runs_then_get_status_reaches_completed():
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/runs", json={"project": "plane", "item_id": "42"})
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        status = "running"
        for _ in range(100):
            await asyncio.sleep(0.01)
            got = await client.get(f"/runs/{run_id}")
            if got.status_code == 200:
                status = got.json().get("status")
                if status == "completed":
                    break
        assert status == "completed"


async def test_get_unknown_run_is_404():
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/runs/does-not-exist")
        assert resp.status_code == 404
