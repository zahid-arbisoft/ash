"""Configuration package.

Two layers (hybrid, plan §9):
- `Settings` (pydantic-settings) — engine-wide secrets, LLM defaults, per-agent model overrides.
- `ProjectConfig` (`projects/<name>.yaml`) — per-client/engagement repo coords, autonomy, budget.

The engine reads no hardcoded repo names; everything project-specific is data.
"""

from ash.config.settings import (
    AgentModelOverride,
    Autonomy,
    Budget,
    IssueSource,
    LLMSettings,
    ProjectConfig,
    Settings,
    WorkTarget,
    get_settings,
    load_project,
)

__all__ = [
    "AgentModelOverride",
    "Autonomy",
    "Budget",
    "IssueSource",
    "LLMSettings",
    "ProjectConfig",
    "Settings",
    "WorkTarget",
    "get_settings",
    "load_project",
]
