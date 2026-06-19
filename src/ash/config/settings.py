"""Hybrid configuration.

`Settings` (env / `.env`) holds engine-wide secrets, the global LLM default, and per-agent model
overrides with global fallback. `ProjectConfig` (`projects/<name>.yaml`) holds the per-engagement
repo topology, autonomy flags, and budget — the multi-tenant "projects are data" layer (plan §9).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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
    """Per-agent overrides; any unset field falls back to the global LLM default.

    `base_url` and `api_key` let one agent bypass the global LiteLLM proxy and hit a
    different endpoint (e.g. Google AI Studio) without changing the global settings.
    """

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    base_url: str | None = None  # overrides LLM_BASE_URL for this agent only
    api_key: str | None = None   # overrides the provider's default API key


class Settings(BaseSettings):
    """Engine-wide settings from environment / `.env` (no secrets committed)."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),  # absolute path so CWD doesn't matter
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

    # Max characters of the work brief sent to each agent. Lower this for small-context models
    # (e.g. BRIEF_MAX_CHARS=3000 for a 4096-token window). Default is effectively unlimited.
    brief_max_chars: int = 100_000

    # Tool-loop tuning — reduce all three for small-context models (e.g. a 4096-token 7B model):
    #   EXPLORE_STEPS=4          — max tool-call rounds in the _explore phase (default 8)
    #   EXPLORE_TOOL_CHARS=1500  — max chars kept from each tool result (default 3000)
    #   EXPLORE_WINDOW=3         — rolling-window: keep last N exchanges in context (0 = keep all)
    # Example .env for Qwen2.5-Coder-7B:
    #   EXPLORE_STEPS=4
    #   EXPLORE_TOOL_CHARS=1500
    #   EXPLORE_WINDOW=3
    explore_steps: int = 8
    explore_tool_chars: int = 3000
    explore_window: int = 0  # 0 = unlimited history; N>0 = keep last N tool exchanges
    # Max chars of phase-1 exploration notes folded into the tool-free _extract prompt.
    # Prevents the extraction request from overflowing a small-context model when the model's
    # free-text conclusion is long. ≈ chars/4 tokens. Lower for tiny windows.
    explore_notes_chars: int = 12_000
    # Max TOTAL chars of changed-file contents dumped into the Reviewer/Fixer prompts.
    # These agents include the full file bodies for review/fix; without a total cap a change
    # touching several or large files overflows a small-context model. ≈ chars/4 tokens.
    files_max_chars: int = 24_000

    # per-agent overrides (nested: AGENT_PM__MODEL, AGENT_REVIEWER__PROVIDER, …)
    agent_pm: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_research: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_coding: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_reviewer: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_fixer: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_rfc: AgentModelOverride = Field(default_factory=AgentModelOverride)

    # vector store (Chroma)
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    # Context minimization (decision #26 / F7) — send only what's needed to the LLM.
    #   CHUNK_MAX_CHARS   — code is indexed as chunks of ~this size (with line ranges), not whole
    #                       files, so semantic search returns the relevant span, not a file head.
    #   CHUNK_OVERLAP     — overlap between adjacent chunks to avoid splitting context mid-symbol.
    #   SEARCH_SNIPPET_CHARS — max chars returned per search hit (was a fixed 800).
    chunk_max_chars: int = 1_400
    chunk_overlap: int = 160
    search_snippet_chars: int = 900
    # Semantic-index guardrails (decision #26 / F7) — keep Research from blocking on a huge repo.
    #   INDEX_MAX_FILES   — above this many indexable files, SKIP local embedding and use the
    #                       grep-based fallback search instead (0 = no cap; index everything).
    #                       Large monorepos (e.g. edx-platform) embed slowly client-side, so the
    #                       agent would sit in "indexing…" for minutes before its first LLM call.
    #   INDEX_PROGRESS_EVERY — log indexing progress every N files so it never looks hung.
    index_max_files: int = 1_500
    index_progress_every: int = 500

    # PM ticket depth (decision #27) — after generating the spec skeleton, run a focused
    # second pass to elaborate tickets so they come out richly detailed (instead of being
    # compressed to fit one all-in-one structured response). `pm_bulk_elaborate` (default True)
    # uses a single bulk LLM call for all tickets to minimize total calls; if False,
    # PM makes one call per ticket. Disable `pm_detail_tickets` to skip elaboration entirely.
    # `pm_detail_context_chars` caps how much of the source spec is fed into the elaboration.
    pm_detail_tickets: bool = True
    pm_bulk_elaborate: bool = True
    pm_detail_context_chars: int = 24_000
    # LLM I/O capture (decision #30) — persist every agent↔LLM exchange (prompt + response) to the
    # agent_llm_exchanges table for the per-run "LLM I/O" view. Set False to disable (privacy/size).
    persist_llm_exchanges: bool = True
    # PM estimates (decision #29) — the LLM fills the estimate fields, then a deterministic Python
    # repair pass normalizes them. `pm_estimate_speedup` is the traditional→LLM divisor used when
    # the model didn't give a sane llm_estimate (traditional_days / this). 5–8× is realistic.
    pm_estimate_speedup: float = 6.0

    # logging
    log_level: str = "INFO"

    def model_for(self, agent: AgentName) -> LLMSettings:
        override: AgentModelOverride = getattr(self, f"agent_{agent}")
        # Per-agent base_url wins over the global; unset (None) falls back to global.
        resolved_base_url = (
            override.base_url if override.base_url is not None else self.llm_base_url
        )
        return LLMSettings(
            provider=override.provider or self.llm_provider,
            model=override.model or self.llm_model,
            temperature=(
                override.temperature if override.temperature is not None else self.llm_temperature
            ),
            max_tokens=(
                override.max_tokens if override.max_tokens is not None else self.llm_max_tokens
            ),
            base_url=resolved_base_url,
        )

    def api_key_for(self, provider: str) -> str:
        return self.anthropic_api_key if provider == "anthropic" else self.openai_api_key

    def effective_api_key(self, agent: AgentName) -> str:
        """Return the API key for `agent`, preferring a per-agent override over the default."""
        override: AgentModelOverride = getattr(self, f"agent_{agent}")
        if override.api_key:
            return override.api_key
        llm = self.model_for(agent)
        return self.api_key_for(llm.provider)


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
    auto_merge_on_approve: bool = False  # Reviewer may merge when it approves (plan §10.4)


TriggerMode = Literal["auto", "manual"]


class AgentPolicy(BaseModel):
    """Per-agent operating policy (plan §10.0 + agent_task_dispatch_plan).

    `trigger` governs *when work starts*: `auto` = dispatcher picks up tasks automatically;
    `manual` = human must click Trigger in the UI. Orthogonal to `Autonomy` (which gates
    dangerous mid-loop steps like merge/push).

    Default is **`manual`** for every agent except PM (see `DEFAULT_AUTO_TRIGGER_AGENTS` /
    `ProjectConfig.agent_policy`): PM runs automatically to produce the spec, then each downstream
    agent waits for an explicit human Trigger unless a project/DB override opts it into `auto`.

    Dispatch limits:
      `concurrency_limit` — max simultaneous in_progress tasks for this agent.
      `daily_quota`       — max tasks completed per calendar day (None = unlimited).
      `max_retries`       — times to retry a failed task before marking it failed permanently.
      `schedule_cron`     — optional cron window (e.g. "0 9-18 * * 1-5"); None = always.
    DB overrides (AgentPolicyRecord) take precedence over YAML values.
    """

    trigger: TriggerMode = "manual"
    enabled: bool = True
    concurrency_limit: int = 1
    daily_quota: int | None = None
    max_retries: int = 0
    schedule_cron: str | None = None


# Agents the UI/engine knows about, in pipeline order. RFC is a placeholder (plan §10.6).
KNOWN_AGENTS: tuple[str, ...] = (
    "pm",
    "research",
    "coding",
    "reviewer",
    "fixer",
    "rfc",
)

# Agents that default to `auto` trigger (run without a manual click). Only PM — it must produce
# the spec automatically; everything downstream defaults to `manual` so a human gates each step.
DEFAULT_AUTO_TRIGGER_AGENTS: frozenset[str] = frozenset({"pm"})


class Budget(BaseModel):
    per_ticket_usd: float = 2.0
    per_day_usd: float = 20.0


class ProjectConfig(BaseModel):
    name: str
    # both optional: a PM-only / attachments run needs neither an issue source nor a work target
    issues: IssueSource | None = None
    work: WorkTarget | None = None
    autonomy: Autonomy = Field(default_factory=Autonomy)
    budget: Budget = Field(default_factory=Budget)
    # where the Research agent publishes its doc: file (default) | comment (source issue) | none
    research_sink: Literal["file", "comment", "none"] = "file"
    # per-agent trigger/enabled policy, keyed by agent name (pm/research/coding/reviewer/fixer/rfc)
    agents: dict[str, AgentPolicy] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)
    skills: str | None = None

    @property
    def runtime_dir(self) -> Path:
        return RUNTIME_DIR / self.name

    def agent_policy(self, name: str) -> AgentPolicy:
        """The policy for an agent. Explicit YAML entry wins; otherwise the default is
        `manual` trigger for every agent except those in `DEFAULT_AUTO_TRIGGER_AGENTS` (PM)."""
        if name in self.agents:
            return self.agents[name]
        if name in DEFAULT_AUTO_TRIGGER_AGENTS:
            return AgentPolicy(trigger="auto")
        return AgentPolicy()


def load_project(name: str) -> ProjectConfig:
    path = PROJECTS_DIR / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in PROJECTS_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"No project config at {path}. Available: {available or '(none)'}")
    data = yaml.safe_load(path.read_text())
    cfg = ProjectConfig.model_validate(data)
    env_path = os.getenv("LOCAL_REPO_PATH")
    if env_path and cfg.work is not None and not cfg.work.local_repo_path:
        cfg.work.local_repo_path = env_path
    return cfg
