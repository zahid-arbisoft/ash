"""Routes — start a run (background) and read its checkpointed status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from ash.api.schemas import RunAccepted, RunRequest
from ash.graph.runner import Runner

router = APIRouter()


def _runner(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=RunAccepted)
async def start_run(req: RunRequest, request: Request) -> RunAccepted:
    run_id = await _runner(request).start_run(
        project=req.project,
        item_id=req.item_id,
        board=req.board,
        intake_mode=req.intake_mode,
        integration_id=req.integration_id,
        spec_file_path=req.spec_file_path,
    )
    return RunAccepted(run_id=run_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    state = await _runner(request).get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown run_id: {run_id}")
    return state
