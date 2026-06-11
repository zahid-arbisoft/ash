"""Server-rendered UI routes (Jinja2). Configuration lives in the SQLAdmin portal at /admin."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.config.settings import PROJECTS_DIR
from ash.db.base import get_session
from ash.db.models import Integration, RunRecord
from ash.graph.runner import Runner

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

INTAKE_MODES = ["raw_to_spec", "spec_ready", "raw_to_dev"]


def _projects() -> list[str]:
    return sorted(p.stem for p in PROJECTS_DIR.glob("*.yaml"))


def _runner(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    integrations = list((await session.execute(select(Integration))).scalars().all())
    runs = list(
        (await session.execute(select(RunRecord).order_by(RunRecord.created_at.desc()).limit(10)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"integrations": integrations, "runs": runs},
    )


@router.get("/ui/integrations", response_class=HTMLResponse)
async def integrations_list(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    integrations = list(
        (await session.execute(select(Integration).order_by(Integration.name))).scalars().all()
    )
    return templates.TemplateResponse(request, "integrations.html", {"integrations": integrations})


@router.get("/ui/runs/new", response_class=HTMLResponse)
async def run_new(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    integrations = list(
        (await session.execute(select(Integration).where(Integration.enabled))).scalars().all()
    )
    return templates.TemplateResponse(
        request,
        "run_new.html",
        {"integrations": integrations, "projects": _projects(), "modes": INTAKE_MODES},
    )


@router.post("/ui/runs")
async def run_create(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    project: Annotated[str, Form()],
    item_id: Annotated[str, Form()],
    intake_mode: Annotated[str, Form()],
    integration_id: Annotated[str, Form()] = "",
) -> RedirectResponse:
    int_id = int(integration_id) if integration_id else None
    run_id = await _runner(request).start_run(
        project=project, item_id=item_id, intake_mode=intake_mode, integration_id=int_id
    )
    session.add(
        RunRecord(
            run_id=run_id,
            project=project,
            integration_id=int_id,
            item_id=item_id,
            intake_mode=intake_mode,
        )
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.get("/ui/runs/{run_id}", response_class=HTMLResponse)
async def run_status(request: Request, run_id: str) -> HTMLResponse:
    state = await _runner(request).get_run(run_id)
    return templates.TemplateResponse(
        request, "run_status.html", {"run_id": run_id, "state": state}
    )
