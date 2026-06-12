"""Routes — start a run (background), read its status, and upload spec files for the PM agent."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from ash.api.schemas import ResumeRequest, RunAccepted, RunRequest, UploadResult
from ash.config.settings import RUNTIME_DIR
from ash.graph.runner import Runner

router = APIRouter()


def _runner(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


@router.post("/uploads", response_model=UploadResult)
async def upload_files(files: list[UploadFile] = File(...)) -> UploadResult:  # noqa: B008
    """Store uploaded spec files and return their paths (pass these as a run's `attachments`)."""
    dest = RUNTIME_DIR / "uploads" / uuid.uuid4().hex
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for f in files:
        name = (f.filename or "file").replace("/", "_")
        target = dest / name
        target.write_bytes(await f.read())
        paths.append(str(target))
    return UploadResult(paths=paths)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=RunAccepted)
async def start_run(req: RunRequest, request: Request) -> RunAccepted:
    run_id = await _runner(request).start_run(
        project=req.project,
        item_id=req.item_id,
        board=req.board,
        intake_mode=req.intake_mode,
        integration_id=req.integration_id,
        attachments=req.attachments,
        task_sink_id=req.task_sink_id,
    )
    return RunAccepted(run_id=run_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    state = await _runner(request).get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    return state


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest, request: Request) -> dict[str, Any]:
    """Resume a run paused at a human-in-the-loop gate with the human's decision."""
    state = await _runner(request).resume_run(run_id, req.decision)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    return state
