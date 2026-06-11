"""App tables: registered issue-source integrations + a lightweight run registry."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ash.db.base import Base
from ash.db.crypto import EncryptedString


class ProviderKind(str, enum.Enum):
    github = "github"
    jira = "jira"
    plane = "plane"


class Integration(Base):
    """A configured issue source (GitHub / Jira / Plane). Secret is encrypted at rest."""

    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[ProviderKind] = mapped_column(Enum(ProviderKind, name="provider_kind"))
    base_url: Mapped[str | None] = mapped_column(String(500), default=None)
    # provider-specific config, e.g. {"repo": "owner/name"} / {"project_key": "ENG"} /
    # {"workspace_slug": "acme", "project_id": "..."}
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret: Mapped[str] = mapped_column(EncryptedString(2000), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __str__(self) -> str:
        return f"{self.name} ({self.kind.value})"


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
