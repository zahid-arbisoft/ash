"""WorkflowState — the per-ticket state that flows through the pipeline.

Built with failure/retry/resume in mind from day one (plan §3): every stage records what happened
so a crash can resume and the heartbeat (Phase 4) can persist progress to state.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from .schemas import Spec


class Stage(str, Enum):
    queued = "queued"
    spec = "spec"
    rfc = "rfc"
    research = "research"
    coding = "coding"
    docs = "docs"
    pr = "pr"
    review = "review"
    fix = "fix"
    done = "done"
    escalated = "escalated"
    failed = "failed"


class Status(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    ok = "ok"
    error = "error"


class Event(BaseModel):
    stage: Stage
    status: Status
    detail: str = ""
    at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WorkflowState(BaseModel):
    project: str
    issue_number: int
    issue_title: str = ""
    issue_url: str = ""

    stage: Stage = Stage.queued
    status: Status = Status.pending
    error: str | None = None
    attempt_count: int = 0

    spec: Spec | None = None
    branch: str | None = None
    worktree_path: str | None = None
    pr_url: str | None = None

    history: list[Event] = Field(default_factory=list)

    def record(self, stage: Stage, status: Status, detail: str = "") -> None:
        self.stage = stage
        self.status = status
        if status == Status.error:
            self.error = detail
        self.history.append(Event(stage=stage, status=status, detail=detail))
