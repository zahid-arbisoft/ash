"""Agents — the software house "staff" as LangGraph nodes."""

from ash.agents.base import BaseAgent
from ash.agents.dev import DevAgent
from ash.agents.fixer import FixerAgent
from ash.agents.intake import IntakeAgent
from ash.agents.pm import PMAgent
from ash.agents.research import ResearchAgent
from ash.agents.reviewer import ReviewerAgent

# Back-compat alias for the coding→dev rename (decision #33).
CodingAgent = DevAgent

__all__ = [
    "BaseAgent",
    "DevAgent",
    "CodingAgent",
    "FixerAgent",
    "IntakeAgent",
    "PMAgent",
    "ResearchAgent",
    "ReviewerAgent",
]
