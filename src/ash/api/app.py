"""FastAPI app factory + lifespan.

Lifespan opens the async Postgres checkpointer (creating its tables on first run), creates the app
tables (integrations / run registry), builds the Runner, and mounts the SQLAdmin portal.
`POST /runs` starts the graph as a background task; `GET /runs/{id}` reads checkpointer state; the
Jinja2 UI is served at `/`, the admin portal at `/admin`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ash.admin import setup_admin
from ash.api.routes import router as api_router
from ash.app_context import build_runner
from ash.config.settings import get_settings
from ash.db.base import get_engine, init_db
from ash.graph.checkpointer import checkpointer_from_dsn
from ash.observability.logging import configure_logging
from ash.web import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_db()  # create app tables (integrations, run records)
    async with checkpointer_from_dsn(settings.postgres_dsn) as saver:
        await saver.setup()
        app.state.runner = build_runner(settings, checkpointer=saver)
        setup_admin(app, get_engine(), settings)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="ASH — Agentic Software House", version="0.1.0", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(web_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
