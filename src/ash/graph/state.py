"""WorkflowState — one root state with **namespaced per-agent sub-states**.

Each agent reads the root state and writes only its own namespace (plan §3 + boilerplate spec §5).
Failure/resume is first-class: every sub-state carries an optional `error`, and the run is marked
`failed` at `merge` if any namespace errored. Persisted via the LangGraph checkpointer per run.

Intake is configurable per run (`intake_mode`): the issue may already be a spec (`spec_ready`), a
raw issue the PM converts (`raw_to_spec`), or a raw issue fed straight to the build team
(`raw_to_dev`). The conditional graph routes accordingly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ash.integrations.base import RawIssue
from ash.schemas import CodeChange, ImplementationPlan, Spec

IntakeMode = Literal["spec_ready", "raw_to_spec", "raw_to_dev"]


class PMState(BaseModel):
    spec: Spec | None = None
    board_ref: str | None = None
    ticket_refs: list[str] = Field(default_factory=list)  # where pushed tickets live (urls/ids)
    comment_url: str | None = None  # set when the deferred post-comment feature lands
    note: str | None = None
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


class IntakeState(BaseModel):
    note: str | None = None
    error: str | None = None


class WorkflowState(BaseModel):
    run_id: str
    project: str
    item_id: str
    board: str = "github"

    # intake configuration (set at run start)
    intake_mode: IntakeMode = "raw_to_spec"
    integration_id: int | None = None
    attachments: list[str] = Field(default_factory=list)  # uploaded spec files PM should read
    task_sink_id: int | None = None  # where PM pushes tickets (None → admin default → file board)

    # discovered during the run
    raw_issue: RawIssue | None = None
    issue_title: str = ""
    issue_url: str = ""

    intake: IntakeState = Field(default_factory=IntakeState)
    pm: PMState = Field(default_factory=PMState)
    research: ResearchState = Field(default_factory=ResearchState)
    coding: CodingState = Field(default_factory=CodingState)
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    fixer: FixerState = Field(default_factory=FixerState)

    status: Literal["running", "completed", "failed"] = "running"

    def brief(self) -> str:
        """The text the build team works from: the PM spec if present, else the raw issue."""
        if self.pm.spec is not None:
            return self.pm.spec.model_dump_json(indent=2)
        if self.raw_issue is not None:
            return f"# {self.raw_issue.title}\n\n{self.raw_issue.body}"
        return ""
