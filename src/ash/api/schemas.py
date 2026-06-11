"""API request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RunRequest(BaseModel):
    project: str
    item_id: str
    board: str = "github"


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
