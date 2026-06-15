"""WorkflowState — one root state with **namespaced per-agent sub-states**.

Each agent reads the root state and writes only its own namespace (plan §3 + boilerplate spec §5).
Failure/resume is first-class: every sub-state carries an optional `error`, and the run is marked
`failed` at `merge` if any namespace errored. Persisted via the LangGraph checkpointer per run.

Intake is configurable per run (`intake_mode`): the issue may already be a spec (`spec_ready`), a
raw issue the PM converts (`raw_to_spec`), or a raw issue fed straight to the build team
(`raw_to_dev`). The conditional graph routes accordingly.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ash.integrations.base import RawIssue
from ash.schemas import CodeChange, CodeReview, ImplementationPlan, Spec

IntakeMode = Literal["spec_ready", "raw_to_spec", "raw_to_dev"]
StoryMode = Literal["single", "multiple"]
StoryStep = Literal["research", "coding", "reviewer", "fixer"]

# Sentinel ticket id used for runs that have no spec (raw_to_dev) — one synthetic story.
RAW_STORY_ID = "_main"


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + (
        f"\n\n[brief truncated at {max_chars} chars — use exploration tools to read more]"
    )


class PMState(BaseModel):
    spec: Spec | None = None
    board_ref: str | None = None
    ticket_refs: list[str] = Field(default_factory=list)  # where pushed tickets live (urls/ids)
    comment_url: str | None = None  # set when the deferred post-comment feature lands
    # ticket ids the human chose to build as stories at the review gate (F1); None = no explicit
    # selection (story_mode default applies). Empty list is treated as "fall back to first ticket".
    story_selection: list[str] | None = None
    note: str | None = None
    error: str | None = None
    tokens: dict[str, int] | None = None  # {"prompt_tokens": N, "completion_tokens": N}


class ResearchState(BaseModel):
    plan: ImplementationPlan | None = None
    branch: str | None = None
    worktree_path: str | None = None
    doc_ref: str | None = None  # where the research doc was published (file path / comment url)
    note: str | None = None  # e.g. "skipped: no local clone configured"
    error: str | None = None
    tokens: dict[str, int] | None = None


class CodingState(BaseModel):
    change: CodeChange | None = None
    files_written: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    # worktree/branch the change was applied on — set by Coding so the Fixer can locate it
    # even when Research was disabled (Research normally records these in ResearchState).
    worktree_path: str | None = None
    branch: str | None = None
    note: str | None = None
    error: str | None = None
    tokens: dict[str, int] | None = None


class ReviewerState(BaseModel):
    review: CodeReview | None = None
    verdict: str | None = None  # "approve" | "request_changes"
    comment_url: str | None = None  # where the review was posted, if a PR exists
    merged: bool = False  # set when auto-merge policy merged the PR
    note: str | None = None
    error: str | None = None
    tokens: dict[str, int] | None = None


class FixerState(BaseModel):
    change: CodeChange | None = None
    files_written: list[str] = Field(default_factory=list)
    iterations: int = 0  # how many fix rounds ran (bounded by MAX_FIX_ITERATIONS)
    pr_url: str | None = None
    note: str | None = None
    error: str | None = None
    tokens: dict[str, int] | None = None


class RFCState(BaseModel):
    doc: str | None = None  # rendered Markdown RFC
    doc_ref: str | None = None  # where the RFC was published (file path or URL)
    title: str | None = None  # RFC title for display
    note: str | None = None
    error: str | None = None
    tokens: dict[str, int] | None = None


class IntakeState(BaseModel):
    note: str | None = None
    error: str | None = None


class StoryState(BaseModel):
    """Per-story build state (decision #26). The story is the unit of execution: each story owns
    its own Research/Coding/Reviewer/Fixer namespaces and produces exactly one PR.

    The build-team agents read/write the flat `WorkflowState.research/coding/reviewer/fixer`
    namespaces; the node adapter hydrates those from the *current* story before each agent runs and
    folds the result back into `stories[ticket_id]` after — so agents stay story-agnostic while
    state is fully per-story.
    """

    ticket_id: str
    title: str = ""
    deps: list[str] = Field(default_factory=list)  # ticket ids this story depends on
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    # deterministic identity → guarantees no duplicate PR on retry/regenerate
    branch: str | None = None
    pr_url: str | None = None
    failed_step: StoryStep | None = None  # which sub-step errored (per-story retry target)

    research: ResearchState = Field(default_factory=ResearchState)
    coding: CodingState = Field(default_factory=CodingState)
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    fixer: FixerState = Field(default_factory=FixerState)

    def has_error(self) -> bool:
        return any(
            ns.error is not None
            for ns in (self.research, self.coding, self.reviewer, self.fixer)
        )


def merge_stories(
    old: dict[str, StoryState] | None, new: dict[str, StoryState] | None
) -> dict[str, StoryState]:
    """Reducer: shallow-merge stories by ticket_id so a node writing one story never clobbers the
    others. New values win on key collision (a re-run of a story overwrites its prior state)."""
    merged = dict(old or {})
    merged.update(new or {})
    return merged


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
    ticket_id: str = ""  # optional: scope the build team (research/coding) to ONE spec ticket
    story_mode: StoryMode = "single"  # PM produces one story (default) or many

    # discovered during the run
    raw_issue: RawIssue | None = None
    issue_title: str = ""
    issue_url: str = ""

    intake: IntakeState = Field(default_factory=IntakeState)
    pm: PMState = Field(default_factory=PMState)
    rfc: RFCState = Field(default_factory=RFCState)
    # Flat build-team namespaces = per-story SCRATCH. The node adapter hydrates these from the
    # current story before an agent runs and writes the result back into `stories[current_story]`.
    research: ResearchState = Field(default_factory=ResearchState)
    coding: CodingState = Field(default_factory=CodingState)
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    fixer: FixerState = Field(default_factory=FixerState)

    # Per-story fan-out (decision #26). `stories` is reducer-merged so concurrent/looped writes
    # don't clobber. `story_order` is dependency-sorted; `current_story` is the sequential cursor.
    stories: Annotated[dict[str, StoryState], merge_stories] = Field(default_factory=dict)
    story_order: list[str] = Field(default_factory=list)
    current_story: str = ""

    status: Literal["running", "completed", "failed"] = "running"

    def active_story(self) -> StoryState | None:
        """The story the cursor currently points at, or None."""
        return self.stories.get(self.current_story) if self.current_story else None

    def brief(self, *, max_chars: int = 0) -> str:
        """The text the build team works from.

        - If a `ticket_id` is set and the spec contains it → a focused brief for that ONE ticket
          (epic context + the ticket), so research/coding build a single ticket per run.
        - Else if a spec exists → the whole spec.
        - Else → the raw issue.

        ``max_chars`` truncates the result for small-context models (0 = no limit).
        """
        if self.pm.spec is not None:
            if self.ticket_id:
                ticket = next(
                    (t for t in self.pm.spec.tickets if t.id == self.ticket_id), None
                )
                if ticket is not None:
                    text = self._ticket_brief(ticket)
                    return _truncate(text, max_chars)
            text = self.pm.spec.model_dump_json(indent=2)
            return _truncate(text, max_chars)
        if self.raw_issue is not None:
            text = f"# {self.raw_issue.title}\n\n{self.raw_issue.body}"
            return _truncate(text, max_chars)
        return ""

    def _ticket_brief(self, ticket: Any) -> str:
        """Render a single-ticket brief with enough epic context to implement it."""
        epic = self.pm.spec.epic if self.pm.spec else None
        lines: list[str] = []
        if epic is not None:
            lines += [f"# Epic: {epic.title}", epic.summary, ""]
        lines += [
            f"## Ticket {ticket.id}: {ticket.title}",
            f"Type: {getattr(ticket.type, 'value', ticket.type)}",
            "",
            ticket.description,
        ]
        if ticket.acceptance_criteria:
            lines += [
                "",
                "### Acceptance criteria",
                *(f"- {a}" for a in ticket.acceptance_criteria),
            ]
        if ticket.dependencies:
            lines += ["", f"Depends on: {', '.join(ticket.dependencies)}"]
        return "\n".join(lines)
