"""API request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RunRequest(BaseModel):
    project: str
    item_id: str = "upload"  # "upload" / "-" for attachment-only runs (no issue to fetch)
    board: str = "github"
    intake_mode: str = "raw_to_spec"  # raw_to_spec | spec_ready | raw_to_dev
    integration_id: int | None = None
    attachments: list[str] = []  # paths returned by POST /uploads
    task_sink_id: int | None = None  # where PM pushes tickets (None → default → file board)
    ticket_id: str = ""  # scope the build to one spec ticket (legacy single-ticket runs)
    story_mode: str = "single"  # PM produces one story (default) or many (decision #26)
    pm_only: bool = False  # PM workbench run — generate/refine spec and stop (decision #29)


class RunAccepted(BaseModel):
    run_id: str


class ResumeRequest(BaseModel):
    # the human's decision for a paused run; shape depends on the interrupt
    # (e.g. "approve" / "reject", or HITL middleware's [{"type": "accept"|"edit"|"reject"}])
    decision: Any = "approve"


class UploadResult(BaseModel):
    paths: list[str]


class RunStatus(BaseModel):
    run_id: str
    project: str
    item_id: str
    status: str
    pm: dict[str, Any]
    research: dict[str, Any]
    coding: dict[str, Any]
    reviewer: dict[str, Any]
    fixer: dict[str, Any]
