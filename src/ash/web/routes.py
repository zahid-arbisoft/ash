"""Server-rendered UI routes (Jinja2). Configuration lives in the SQLAdmin portal at /admin."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.config.settings import PROJECTS_DIR, RUNTIME_DIR
from ash.db.base import get_session
from ash.db.models import Integration, RunRecord, TaskSink
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
    sinks = list((await session.execute(select(TaskSink).where(TaskSink.enabled))).scalars().all())
    return templates.TemplateResponse(
        request,
        "run_new.html",
        {
            "integrations": integrations,
            "sinks": sinks,
            "projects": _projects(),
            "modes": INTAKE_MODES,
        },
    )


@router.post("/ui/runs")
async def run_create(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    project: Annotated[str, Form()],
    item_id: Annotated[str, Form()] = "upload",
    intake_mode: Annotated[str, Form()] = "raw_to_spec",
    integration_id: Annotated[str, Form()] = "",
    task_sink_id: Annotated[str, Form()] = "",
    attachments: list[UploadFile] = File(default=[]),  # noqa: B008 — FastAPI form dependency
) -> RedirectResponse:
    int_id = int(integration_id) if integration_id else None
    sink_id = int(task_sink_id) if task_sink_id else None
    paths = await _save_uploads(attachments)
    run_id = await _runner(request).start_run(
        project=project,
        item_id=item_id or "upload",
        intake_mode=intake_mode,
        integration_id=int_id,
        attachments=paths,
        task_sink_id=sink_id,
    )
    session.add(
        RunRecord(
            run_id=run_id,
            project=project,
            integration_id=int_id,
            item_id=item_id or "upload",
            intake_mode=intake_mode,
        )
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


async def _save_uploads(files: list[UploadFile]) -> list[str]:
    real = [f for f in files if f.filename]
    if not real:
        return []
    dest = RUNTIME_DIR / "uploads" / uuid.uuid4().hex
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for f in real:
        target = dest / (f.filename or "file").replace("/", "_")
        target.write_bytes(await f.read())
        paths.append(str(target))
    return paths


@router.get("/ui/runs/{run_id}", response_class=HTMLResponse)
async def run_status(request: Request, run_id: str) -> HTMLResponse:
    state = await _runner(request).get_run(run_id)
    return templates.TemplateResponse(
        request, "run_status.html", {"run_id": run_id, "state": state}
    )
