"""WorkflowState — one root state with **namespaced per-agent sub-states**.

Each agent reads the root state and writes only its own namespace (plan §3 + boilerplate spec §5).
Failure/resume is first-class: every sub-state carries an optional `error`, and the run is marked
`failed` at `merge` if any namespace errored. Persisted via the LangGraph checkpointer per run.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ash.schemas import CodeChange, ImplementationPlan, Spec


class PMState(BaseModel):
    spec: Spec | None = None
    board_ref: str | None = None
    comment_url: str | None = None  # set when the deferred post-comment feature lands
    error: str | None = None


class ResearchState(BaseModel):
    plan: ImplementationPlan | None = None
    branch: str | None = None
    worktree_path: str | None = None
    note: str | None = None  # e.g. "skipped: no local clone configured"
    error: str | None = None


class CodingState(BaseModel):
    change: CodeChange | None = None
    files_written: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    note: str | None = None
    error: str | None = None


class ReviewerState(BaseModel):
    note: str | None = None
    error: str | None = None


class FixerState(BaseModel):
    note: str | None = None
    error: str | None = None


class WorkflowState(BaseModel):
    run_id: str
    project: str
    item_id: str
    board: str = "github"
    issue_title: str = ""
    issue_url: str = ""

    pm: PMState = Field(default_factory=PMState)
    research: ResearchState = Field(default_factory=ResearchState)
    coding: CodingState = Field(default_factory=CodingState)
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    fixer: FixerState = Field(default_factory=FixerState)

    status: Literal["running", "completed", "failed"] = "running"
