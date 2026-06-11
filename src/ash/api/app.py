"""FastAPI app factory + lifespan.

Lifespan opens the async Postgres checkpointer (and creates its tables on first run), builds the
Runner, and stores it on `app.state`. `POST /runs` starts the graph as a background task and returns
a `run_id`; `GET /runs/{run_id}` reads status/state from the checkpointer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ash.api.routes import router
from ash.app_context import build_runner
from ash.config.settings import get_settings
from ash.graph.checkpointer import checkpointer_from_dsn


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    async with checkpointer_from_dsn(settings.postgres_dsn) as saver:
        await saver.setup()
        app.state.runner = build_runner(settings, checkpointer=saver)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="ASH — Agentic Software House", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
