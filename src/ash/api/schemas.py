"""API request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RunRequest(BaseModel):
    project: str = ""
    item_id: str | None = None
    board: str = "github"
    intake_mode: str = "raw_to_spec"  # raw_to_spec | spec_ready | raw_to_dev | spec_file
    integration_id: int | None = None
    spec_file_path: str | None = None


class RunAccepted(BaseModel):
    run_id: str


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
