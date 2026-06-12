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
    title: str
    description: str
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
        description="IDs of tickets that must land first, e.g. ['T1']. Use an empty list if none.",
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
