"""Configuration: engine-wide LLM settings (env) + per-project config (projects/<name>.yaml).

The engine code reads no hardcoded repo names — everything project-specific is data loaded here,
so onboarding a new org project is "add a YAML file", not "change the code" (see plan §9).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


def _find_repo_root() -> Path:
    """Locate the monorepo root by walking up until we find the `projects/` dir (or pyproject).

    Robust to the engine living under engine/src/ash/ — we don't hardcode depth. Allows an
    explicit override via ASH_ROOT for unusual deployments.
    """
    override = os.getenv("ASH_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "projects").is_dir() or (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[3]  # fallback: repo root above engine/src/ash


REPO_ROOT = _find_repo_root()
PROJECTS_DIR = REPO_ROOT / "projects"
RUNTIME_DIR = REPO_ROOT / "runtime"


# ── Engine-wide settings (from environment) ──────────────────────────────────────────────


class LLMSettings(BaseModel):
    provider: str = Field(default="anthropic")  # "anthropic" | "openai"
    api_key: str = ""
    base_url: str | None = None
    models: dict[str, str] = Field(default_factory=dict)  # role -> model id
    max_tokens: int = 8192
    temperature: float = 0.2

    def model_for(self, role: str) -> str:
        try:
            return self.models[role]
        except KeyError as exc:
            raise KeyError(f"No model configured for role '{role}'") from exc


def load_llm_settings() -> LLMSettings:
    return LLMSettings(
        provider=os.getenv("LLM_PROVIDER", "anthropic").lower(),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL") or None,
        models={
            "pm": os.getenv("PM_MODEL", "claude-opus-4-7"),
            "dev": os.getenv("DEV_MODEL", "claude-sonnet-4-6"),
            "reviewer": os.getenv("REVIEWER_MODEL", "claude-sonnet-4-6"),
            "fixer": os.getenv("FIXER_MODEL", "claude-sonnet-4-6"),
        },
    )


# ── Per-project config (from projects/<name>.yaml) ───────────────────────────────────────


class IssueSource(BaseModel):
    source_repo: str  # "owner/repo" — read-only source of issues
    filters: dict = Field(default_factory=dict)


class WorkTarget(BaseModel):
    target_repo: str  # "owner/repo" — where we write branches/PRs/merges (the fork)
    base_branch: str = "main"
    mode: str = "fork"  # "fork" | "single" (see plan §7a)
    upstream_remote: str | None = None  # "owner/repo" of upstream (fork mode only)
    open_upstream_prs: bool = False
    local_repo_path: str | None = None  # path to an existing local clone of target_repo

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
    schedule: dict = Field(default_factory=dict)
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
    # env override keeps machine-specific clone paths out of committed YAML
    env_path = os.getenv("LOCAL_REPO_PATH")
    if env_path and not cfg.work.local_repo_path:
        cfg.work.local_repo_path = env_path
    return cfg
