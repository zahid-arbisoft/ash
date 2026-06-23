"""App tables: a unified connector registry + a lightweight run registry."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ash.db.base import Base
from ash.db.crypto import EncryptedString


class ConnectorKind(str, enum.Enum):
    """The system a connector talks to. The same system can be a source and/or a sink."""

    github = "github"
    jira = "jira"
    plane = "plane"
    file = "file"  # local board (Markdown/JSON) — sink only; the default fallback
    sheets = "sheets"  # Google Sheets (sink, later)


class Connector(Base):
    """A single configured connection to an external system. Secret is encrypted at rest.

    One connector can be used as an issue **source** (PM reads issues from it), a ticket **sink**
    (PM creates tickets in it), or both — toggled via `is_source` / `is_sink`. This replaces the
    former separate `Integration` (source) and `TaskSink` (sink) tables, so a system like Jira is
    configured once. The default sink (used when a run doesn't pick one) is `is_default_sink`.
    """

    __tablename__ = "connectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[ConnectorKind] = mapped_column(Enum(ConnectorKind, name="connector_kind"))
    # how we reach the system: None/"" = our built-in httpx client; "http" = a hosted MCP server
    # (tools loaded via langchain-mcp-adapters from `base_url`, auth from `secret`/config headers)
    transport: Mapped[str | None] = mapped_column(String(20), default=None)
    base_url: Mapped[str | None] = mapped_column(String(500), default=None)
    # system-specific config, e.g. {"repo": "owner/name"} / {"project_key": "ENG", "email": "..."} /
    # {"workspace_slug": "acme", "project_id": "..."}
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret: Mapped[str] = mapped_column(EncryptedString(2000), default="")
    is_source: Mapped[bool] = mapped_column(Boolean, default=False)  # read issues from it
    is_sink: Mapped[bool] = mapped_column(Boolean, default=False)  # create tickets in it
    is_default_sink: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __str__(self) -> str:
        roles = "/".join(r for r, on in (("source", self.is_source), ("sink", self.is_sink)) if on)
        return f"{self.name} ({self.kind.value}: {roles or 'unused'})"


class AdminUser(Base):
    """An admin-portal login. Password is stored as a PBKDF2-SHA256 hash, never plaintext."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return self.username


class RunRecord(Base):
    """A lightweight record of a started run (live status is read from the checkpointer).

    `status` is a best-effort denormalized copy of the final run status (running / completed /
    failed / awaiting_review) so the runs list can render a badge without loading every
    checkpoint; the checkpointer remains the source of truth for live state.
    """

    __tablename__ = "run_records"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str] = mapped_column(String(200))
    integration_id: Mapped[int | None] = mapped_column(default=None)
    task_sink_id: Mapped[int | None] = mapped_column(default=None)  # where PM pushed tickets
    item_id: Mapped[str] = mapped_column(String(200))
    intake_mode: Mapped[str] = mapped_column(String(40))
    ticket_id: Mapped[str] = mapped_column(String(120), default="")  # build scoped to one ticket
    story_mode: Mapped[str] = mapped_column(String(20), default="single")  # single | multiple
    # PR packaging for multi-story runs (F7): per_story (one PR each) | single (one combined PR).
    pr_strategy: Mapped[str] = mapped_column(String(20), default="per_story")
    # Workflow this run executed with (workflow-builder): id for reference + an immutable snapshot
    # of the workflow's steps at start, so editing the workflow never changes past/in-flight runs.
    workflow_id: Mapped[int | None] = mapped_column(Integer, default=None)
    workflow_snapshot: Mapped[Any | None] = mapped_column(JSON, default=None)
    pm_only: Mapped[bool] = mapped_column(Boolean, default=False)  # PM workbench run (decision #29)
    status: Mapped[str] = mapped_column(String(40), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"{self.run_id} [{self.project}#{self.item_id}]"


class SpecRecord(Base):
    """A persisted PM spec, one per run. Powers the searchable PM-runs view (plan §10.1, U2).

    The full spec is stored as JSON; `epic_title` / `summary` are denormalized for fast text
    search and list previews. Upserted by `run_id` when PM (re)generates the spec.
    """

    __tablename__ = "spec_records"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str] = mapped_column(String(200))
    item_id: Mapped[str] = mapped_column(String(200))
    intake_mode: Mapped[str] = mapped_column(String(40), default="raw_to_spec")
    epic_title: Mapped[str] = mapped_column(String(500), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    ticket_count: Mapped[int] = mapped_column(Integer, default=0)
    spike_count: Mapped[int] = mapped_column(Integer, default=0)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    board_ref: Mapped[str | None] = mapped_column(String(500), default=None)
    ticket_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)  # urls/ids once pushed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __str__(self) -> str:
        return f"spec[{self.run_id}] {self.epic_title}"


class AgentTask(Base):
    """One unit of work for one agent within one pipeline run.

    Created as a side-effect when each pipeline stage becomes ready (e.g. pm_publish approval
    creates a Research task). Status progresses:
      pending → in_progress → completed | failed | cancelled.
    On failure, the dispatcher resets to `pending` and increments `retry_count` until
    `max_retries` is reached, then marks `failed`.
    """

    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(40))  # pm|research|coding|reviewer|fixer|rfc
    project: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[str] = mapped_column(String(64))  # matches RunRecord.run_id
    # per-story scoping (decision #26): "" = run-level (intake/pm/rfc); else the story's ticket id
    ticket_id: Mapped[str] = mapped_column(String(120), default="")
    item_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending|scheduled|in_progress|completed|failed|cancelled
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)  # snapshot at task creation
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    result_ref: Mapped[str | None] = mapped_column(String(1000), default=None)  # PR url / doc path
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_agent_tasks_agent_project_status", "agent_name", "project", "status"),
        Index("ix_agent_tasks_run_agent", "run_id", "agent_name"),
        Index("ix_agent_tasks_run_agent_ticket", "run_id", "agent_name", "ticket_id"),
        Index("ix_agent_tasks_agent_project_created", "agent_name", "project", "created_at"),
    )

    def __str__(self) -> str:
        return f"AgentTask[{self.agent_name}/{self.project}#{self.item_id} {self.status}]"


class StoryRecord(Base):
    """One persisted story within a run (decision #26 / F2).

    Survives process restarts so the no-duplicate-PR check (branch/pr_url per ticket) and the
    per-story UI (progress, PR dropdown) work even when the checkpoint isn't loaded. Upserted by
    (run_id, ticket_id) as the story advances.
    """

    __tablename__ = "story_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    ticket_id: Mapped[str] = mapped_column(String(120))
    project: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    branch: Mapped[str | None] = mapped_column(String(300), default=None)
    pr_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    failed_step: Mapped[str | None] = mapped_column(String(40), default=None)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order in story_order
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_story_records_run_ticket", "run_id", "ticket_id", unique=True),
    )

    def __str__(self) -> str:
        return f"Story[{self.run_id}/{self.ticket_id} {self.status}]"


class AgentRunMetric(Base):
    """Analytics: one row per agent execution (decision #26 / F8).

    Captures tokens (in/out) + wall-clock duration + model for each agent run (including each
    retry/regenerate), keyed to the run and — for build-team agents — the story (`ticket_id`).
    Powers per-run / per-story / per-agent / per-project rollups in the UI.
    """

    __tablename__ = "agent_run_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    project: Mapped[str] = mapped_column(String(200))
    ticket_id: Mapped[str | None] = mapped_column(String(120), default=None)
    agent_name: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_agent_run_metrics_run", "run_id"),
        Index("ix_agent_run_metrics_run_ticket", "run_id", "ticket_id"),
        Index("ix_agent_run_metrics_agent_project", "agent_name", "project"),
    )

    def __str__(self) -> str:
        return f"Metric[{self.run_id}/{self.agent_name} {self.total_tokens}tok]"


class AgentLLMExchange(Base):
    """One agent↔LLM exchange (decision #30) — the messages sent to the model and the response.

    Persisted so a human can inspect exactly what produced a spec/plan/code. One agent run() can
    emit several rows (e.g. PM: spec + repair + per-ticket elaborate). Keyed to the run and — for
    build-team agents — the story (`ticket_id`). Plain app table (created by create_all). Message
    content is clipped at capture time to keep rows bounded.
    """

    __tablename__ = "agent_llm_exchanges"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    project: Mapped[str] = mapped_column(String(200), default="")
    ticket_id: Mapped[str | None] = mapped_column(String(120), default=None)
    agent_name: Mapped[str] = mapped_column(String(40))
    phase: Mapped[str] = mapped_column(String(20), default="")  # single_call|explore|extract|refine
    step: Mapped[int] = mapped_column(Integer, default=0)  # explore-loop ordinal; 0 otherwise
    model: Mapped[str] = mapped_column(String(200), default="")
    request: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{role, content}]
    # {content, tool_calls?, parsed?}
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Explicitly extracted primary context (e.g. source requirements/spec/brief)
    # for easier visibility and grouping in the UI.
    context: Mapped[str | None] = mapped_column(Text, default=None)
    # Any specific code snippets sent as primary grounding (not via tools).
    code: Mapped[str | None] = mapped_column(Text, default=None)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_agent_llm_exchanges_run", "run_id"),)

    def __str__(self) -> str:
        return f"Exchange[{self.run_id}/{self.agent_name}/{self.phase}]"


class AgentPolicyRecord(Base):
    """DB-backed policy overrides for per-agent dispatch settings.

    Resolution order: AgentPolicyRecord (this table, highest priority)
                      > AgentPolicy in projects/<name>.yaml
                      > code defaults
    Allows live UI tuning without editing YAML or redeploying.
    """

    __tablename__ = "agent_policy_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    project: Mapped[str] = mapped_column(String(200))
    agent_name: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(10), default="auto")  # auto | manual
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)
    daily_quota: Mapped[int | None] = mapped_column(Integer, default=None)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    schedule_cron: Mapped[str | None] = mapped_column(String(100), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_agent_policy_records_project_agent", "project", "agent_name", unique=True),
    )

    def __str__(self) -> str:
        return f"AgentPolicy[{self.project}/{self.agent_name} trigger={self.trigger}]"


class Workflow(Base):
    """A reusable, named agent flow (workflow-builder change).

    `steps` is an ordered JSON list of ``{"agent": str, "trigger": "auto"|"manual",
    "enabled": bool}``. In v1 (OD1) execution follows the canonical pipeline order; the workflow
    contributes each agent's enabled/trigger as a per-run default (precedence: AgentPolicyRecord >
    workflow > YAML > code default). A run snapshots the workflow at start so later edits don't
    change in-flight/past runs. Soft-deleted via `disabled` (excluded from the run dropdown, still
    readable for historical runs). At most one workflow per (scope) is `is_default`.
    """

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[Any] = mapped_column(JSON, default=list)
    # how the run defaults its per-story controls: all | selected | one_by_one
    story_execution: Mapped[str] = mapped_column(String(20), default="all")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)  # soft delete
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on every edit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __str__(self) -> str:
        return f"Workflow[{self.id}:{self.name} v{self.version}{' default' if self.is_default else ''}]"
