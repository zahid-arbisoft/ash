"""Structured spec the PM Agent produces. These Pydantic models double as the JSON Schema
handed to the LLM (tool/function calling), so the model is forced to return this exact shape.
Mirrors the output schema in agent_architecture.md: epic / technical_spec / tickets / risks.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Epic(BaseModel):
    title: str
    summary: str = Field(
        description="Plain-language description of the problem and desired outcome"
    )
    business_goal: str = Field(description="Why this matters to users/the product")
    acceptance_criteria: list[str] = Field(
        description="Concrete, testable conditions that define 'done'. Must be an array of strings."
    )
    edge_cases: list[str] = Field(
        default_factory=list,
        description="Edge cases or boundary conditions to watch for. Use an empty list if none.",
    )


class TechnicalSpec(BaseModel):
    approach: str = Field(description="High-level implementation approach")
    affected_areas: list[str] = Field(
        default_factory=list,
        description="Modules/files/services likely to change. Use an empty list if none.",
    )
    data_model_changes: list[str] = Field(
        default_factory=list,
        description="Data model changes, each as a short string. Use an empty list if none.",
    )
    api_changes: list[str] = Field(
        default_factory=list,
        description="API or interface changes, each as a short string. Use an empty list if none.",
    )
    testing_strategy: str = Field(description="How the change will be verified")


class TicketType(str, Enum):
    feature = "feature"
    bug = "bug"
    refactor = "refactor"
    test = "test"
    docs = "docs"
    chore = "chore"
    spike = "spike"  # investigation needed before implementation (handed to Research)


class Ticket(BaseModel):
    id: str = Field(description="Short stable id, e.g. T1, T2")
    title: str = Field(
        description=(
            "Action-oriented title starting with a verb, e.g. "
            "'Add rate-limiting middleware to /api/runs' or 'Fix null-pointer in ConnectorAdmin'."
        )
    )
    description: str = Field(
        description=(
            "Full implementation description a developer can act on without asking questions. "
            "Must cover: (1) what needs to be done and why — the user-facing or system motivation; "
            "(2) the concrete implementation approach — which files/modules/APIs are involved, "
            "what changes are needed, and any key design decisions or constraints; "
            "(3) what is explicitly out of scope for this ticket; "
            "(4) any gotchas, edge cases, or dependencies on other tickets or external systems. "
            "Write at least 4-6 sentences. Be specific — reference real module names, "
            "endpoints, or schema fields from the codebase context where known."
        )
    )
    type: TicketType
    needs_research: bool = Field(
        default=False,
        description="True if this ticket needs a research spike before it can be implemented; "
        "the Research agent picks these up. PM sets this for unclear/risky work.",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Testable done-conditions for this ticket. Use an empty list if none.",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of tickets that must be completed first, e.g. ['T1']. Must reference real ticket "
            "ids and form an acyclic graph — no cycles, no self-reference. Foundational tickets "
            "(shared infrastructure, data layer, encryption) have none. Use an empty list if none."
        ),
    )
    estimate: str = Field(default="", description="Rough size, e.g. S/M/L or hours")


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Risk(BaseModel):
    description: str
    severity: Severity
    mitigation: str


class Spec(BaseModel):
    epic: Epic
    technical_spec: TechnicalSpec
    tickets: list[Ticket]
    risk_assessment: list[Risk] = Field(default_factory=list)
    open_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Unknowns, ambiguities, or unstated decisions that must be resolved before or during "
            "implementation. Examples: an undefined external import/export format, an unspecified "
            "UI framework, an undecided target platform or OS, a missing API contract, or an "
            "integration whose shape is not yet defined. "
            "Record them here instead of guessing or inventing details. "
            "For greenfield projects with external integrations or undecided technology choices, "
            "this list should rarely be empty — each undefined external or undecided stack choice "
            "is a candidate."
        ),
    )


# ── Research/Spike agent output ──────────────────────────────────────────────


class ImplementationPlan(BaseModel):
    summary: str = Field(description="What we'll change and why, grounded in the actual codebase")
    relevant_files: list[str] = Field(
        default_factory=list, description="Existing repo files to modify (paths relative to root)"
    )
    new_files: list[str] = Field(
        default_factory=list, description="New files to create (paths relative to root)"
    )
    steps: list[str] = Field(default_factory=list, description="Ordered implementation steps")
    open_questions: list[str] = Field(default_factory=list)


# ── Dev/Coding agent output ──────────────────────────────────────────────────


class EditAction(str, Enum):
    create = "create"
    modify = "modify"


class FileEdit(BaseModel):
    path: str = Field(description="Path relative to repo root")
    action: EditAction
    content: str = Field(description="The FULL new content of the file (not a diff)")
    rationale: str = ""


class CodeChange(BaseModel):
    summary: str = Field(description="One-paragraph description of the change for the PR body")
    edits: list[FileEdit] = Field(default_factory=list)
    tests_note: str = Field(default="", description="What was/should be tested")


# ── Reviewer agent output ────────────────────────────────────────────────────


class ReviewSeverity(str, Enum):
    """Severity tags for review findings (plan §10.4)."""

    nit = "nit"  # nice-to-have / stylistic
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ReviewVerdict(str, Enum):
    approve = "approve"
    request_changes = "request_changes"


class ReviewFinding(BaseModel):
    path: str = Field(description="Repo-relative file path the finding refers to")
    line: int | None = Field(
        default=None, description="1-based line number if known, else null for a file-level note"
    )
    severity: ReviewSeverity
    category: str = Field(
        default="", description="Short category, e.g. 'bug', 'security', 'style', 'tests'"
    )
    comment: str = Field(description="The review comment — what is wrong and why it matters")
    suggestion: str = Field(default="", description="Concrete fix suggestion, if any")


class CodeReview(BaseModel):
    """A complete, single-pass review of a PR (plan §10.4). Deep in one go to avoid cycles."""

    summary: str = Field(description="Overall assessment of the change for the PR review body")
    findings: list[ReviewFinding] = Field(
        default_factory=list,
        description="All issues found, each tagged by severity. Empty list = nothing to fix.",
    )
    verdict: ReviewVerdict = Field(
        description="approve if the change is correct and complete; request_changes otherwise"
    )

    def blocking(self) -> list[ReviewFinding]:
        """Findings that block merge — high/critical severity."""
        return [
            f
            for f in self.findings
            if f.severity in (ReviewSeverity.high, ReviewSeverity.critical)
        ]


# ── RFC agent output ─────────────────────────────────────────────────────────


class RFCDocument(BaseModel):
    """Structured RFC document generated by the RFC agent (plan §10.6)."""

    title: str = Field(description="Short title for the RFC, e.g. 'RFC-001: Add rate limiting'")
    background: str = Field(description="Context and motivation for this RFC")
    problem_statement: str = Field(description="Precise description of the problem being solved")
    proposed_solution: str = Field(
        description="Detailed description of the proposed approach and design"
    )
    alternatives_considered: str = Field(
        default="",
        description="Other solutions considered and why they were not chosen",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Testable conditions that define when this RFC is implemented",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Unresolved questions that must be answered before or during implementation",
    )

    def to_markdown(self) -> str:
        """Render the RFC as a Markdown document."""
        lines = [
            f"# {self.title}",
            "",
            "## Background",
            self.background,
            "",
            "## Problem Statement",
            self.problem_statement,
            "",
            "## Proposed Solution",
            self.proposed_solution,
        ]
        if self.alternatives_considered:
            lines += ["", "## Alternatives Considered", self.alternatives_considered]
        if self.acceptance_criteria:
            lines += ["", "## Acceptance Criteria"]
            lines += [f"- {c}" for c in self.acceptance_criteria]
        if self.open_questions:
            lines += ["", "## Open Questions"]
            lines += [f"- {q}" for q in self.open_questions]
        lines += ["", "---", "_Generated by the ASH RFC agent._"]
        return "\n".join(lines)


# ── Connector kind-specific config schemas (C1) ──────────────────────────────


class GitHubConnectorConfig(BaseModel):
    """Config fields for a GitHub connector (source and/or sink)."""

    repo: str = Field(description="owner/repo, e.g. 'acme/backend'")
    default_branch: str = Field(default="main", description="Base branch for PRs")


class JiraConnectorConfig(BaseModel):
    """Config fields for a Jira connector."""

    project_key: str = Field(description="Jira project key, e.g. 'ENG'")
    workspace_slug: str = Field(description="Atlassian workspace subdomain (*.atlassian.net)")
    email: str = Field(description="Jira account email used for API auth")


class PlaneConnectorConfig(BaseModel):
    """Config fields for a Plane.so connector."""

    workspace_slug: str = Field(description="Plane workspace slug")
    project_id: str = Field(description="UUID of the Plane project")


class MCPHTTPConnectorConfig(BaseModel):
    """Config for a generic MCP-over-HTTP connector."""

    base_url: str = Field(description="Base URL of the hosted MCP server, e.g. https://mcp.acme.io")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra headers to send (e.g. {'X-API-Version': '2'}). Auth goes in secret.",
    )


class FileConnectorConfig(BaseModel):
    """Config for a local file-board connector (default sink fallback)."""

    board_path: str = Field(
        default="runtime/{project}/board.json",
        description="Path template for the board file (relative to repo root)",
    )
