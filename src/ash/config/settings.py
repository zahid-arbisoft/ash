"""Hybrid configuration.

`Settings` (env / `.env`) holds engine-wide secrets, the global LLM default, and per-agent model
overrides with global fallback. `ProjectConfig` (`projects/<name>.yaml`) holds the per-engagement
repo topology, autonomy flags, and budget — the multi-tenant "projects are data" layer (plan §9).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentName = str  # "pm" | "research" | "coding" | "reviewer" | "fixer"


def _find_repo_root() -> Path:
    """Locate the repo root by walking up until we find `projects/` (or `pyproject.toml`).

    Robust to the package living under `src/ash/`. `ASH_ROOT` overrides for unusual deployments.
    """
    override = os.getenv("ASH_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "projects").is_dir() or (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[3]


REPO_ROOT = _find_repo_root()
PROJECTS_DIR = REPO_ROOT / "projects"
RUNTIME_DIR = REPO_ROOT / "runtime"


# ── LLM settings (engine-wide default + per-agent overrides) ─────────────────────────────


class LLMSettings(BaseModel):
    """A fully-resolved model config for one agent (provider + model + sampling + endpoint)."""

    provider: str = "anthropic"  # "anthropic" | "openai" (openai = any OpenAI-compatible host)
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0
    max_tokens: int = 8192
    base_url: str | None = None  # point "openai" at LiteLLM/Ollama/vLLM/local gateway


class AgentModelOverride(BaseModel):
    """Per-agent overrides; any unset field falls back to the global LLM default."""

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class Settings(BaseSettings):
    """Engine-wide settings from environment / `.env` (no secrets committed)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # secrets / connections
    github_token: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_base_url: str | None = None  # global openai-compatible endpoint override
    postgres_dsn: str = "postgresql://ash:ash@localhost:5432/ash"

    # app DB secret encryption (Fernet key) + admin portal auth.
    # generate a key:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_key: str = ""
    admin_user: str = "admin"
    admin_password: str = "admin"  # noqa: S105 — dev default; override in .env

    # llm global default (flat env vars: LLM_PROVIDER, LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS)
    llm_provider: str = "anthropic"  # "anthropic" | "openai" (openai = any OpenAI-compatible host)
    llm_model: str = "claude-sonnet-4-6"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8192

    # per-agent overrides (nested: AGENT_PM__MODEL, AGENT_REVIEWER__PROVIDER, …)
    agent_pm: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_research: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_coding: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_reviewer: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_fixer: AgentModelOverride = Field(default_factory=AgentModelOverride)

    # logging
    log_level: str = "INFO"

    def model_for(self, agent: AgentName) -> LLMSettings:
        override: AgentModelOverride = getattr(self, f"agent_{agent}")
        return LLMSettings(
            provider=override.provider or self.llm_provider,
            model=override.model or self.llm_model,
            temperature=(
                override.temperature if override.temperature is not None else self.llm_temperature
            ),
            max_tokens=(
                override.max_tokens if override.max_tokens is not None else self.llm_max_tokens
            ),
            base_url=self.llm_base_url,
        )

    def api_key_for(self, provider: str) -> str:
        return self.anthropic_api_key if provider == "anthropic" else self.openai_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ── Per-project config (from projects/<name>.yaml) ───────────────────────────────────────


class IssueSource(BaseModel):
    source_repo: str  # "owner/repo" — read-only source of issues
    filters: dict[str, Any] = Field(default_factory=dict)


class WorkTarget(BaseModel):
    target_repo: str  # "owner/repo" — where we write branches/PRs/merges (the fork)
    base_branch: str = "main"
    mode: str = "fork"  # "fork" | "single" (plan §7a)
    upstream_remote: str | None = None
    open_upstream_prs: bool = False
    local_repo_path: str | None = None

    def resolved_local_path(self) -> Path | None:
        return Path(self.local_repo_path).expanduser() if self.local_repo_path else None


class Autonomy(BaseModel):
    require_human_for_merge: bool = True
    require_human_for_escalation: bool = True


class Budget(BaseModel):
    per_ticket_usd: float = 2.0
    per_day_usd: float = 20.0


class ProjectConfig(BaseModel):
    name: str
    issues: IssueSource
    work: WorkTarget
    autonomy: Autonomy = Field(default_factory=Autonomy)
    budget: Budget = Field(default_factory=Budget)
    schedule: dict[str, Any] = Field(default_factory=dict)
    skills: str | None = None

    @property
    def runtime_dir(self) -> Path:
        return RUNTIME_DIR / self.name


def load_project(name: str) -> ProjectConfig:
    path = PROJECTS_DIR / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in PROJECTS_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"No project config at {path}. Available: {available or '(none)'}")
    data = yaml.safe_load(path.read_text())
    cfg = ProjectConfig.model_validate(data)
    env_path = os.getenv("LOCAL_REPO_PATH")
    if env_path and not cfg.work.local_repo_path:
        cfg.work.local_repo_path = env_path
    return cfg
