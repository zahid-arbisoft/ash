"""App tables: a unified connector registry + a lightweight run registry."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, String, func
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
    """A lightweight record of a started run (live status is read from the checkpointer)."""

    __tablename__ = "run_records"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project: Mapped[str] = mapped_column(String(200))
    integration_id: Mapped[int | None] = mapped_column(default=None)
    item_id: Mapped[str] = mapped_column(String(200))
    intake_mode: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __str__(self) -> str:
        return f"{self.run_id} [{self.project}#{self.item_id}]"
