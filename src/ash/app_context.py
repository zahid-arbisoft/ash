"""Composition root — build agents, the graph, and a Runner from settings.

This is the only place that wires concrete agents together. Tests can inject a fake issue provider
(intake) and PM model to keep runs deterministic and offline.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ash.agents.coding import CodingAgent
from ash.agents.fixer import FixerAgent
from ash.agents.intake import IntakeAgent
from ash.agents.pm import PMAgent
from ash.agents.research import ResearchAgent
from ash.agents.reviewer import ReviewerAgent
from ash.config.settings import Settings
from ash.graph.builder import build_graph
from ash.graph.nodes import Agent
from ash.graph.runner import Runner
from ash.integrations.base import IssueProvider


def build_agents(
    settings: Settings,
    *,
    intake_provider: IssueProvider | None = None,
    pm_model: BaseChatModel | None = None,
) -> dict[str, Agent]:
    return {
        "intake": IntakeAgent(settings, provider=intake_provider),
        "pm": PMAgent(settings, model=pm_model),
        "research": ResearchAgent(settings),
        "coding": CodingAgent(settings),
        "reviewer": ReviewerAgent(settings),
        "fixer": FixerAgent(settings),
    }


def build_runner(settings: Settings, *, checkpointer: Any) -> Runner:
    graph = build_graph(build_agents(settings), checkpointer=checkpointer)
    return Runner(graph=graph)
