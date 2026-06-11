"""Fixer agent — STUB. TODO (Phase 3): apply minimal patches from review feedback (bounded loop)."""

from __future__ import annotations

from typing import Any

from ash.agents.base import BaseAgent
from ash.graph.state import WorkflowState


class FixerAgent(BaseAgent):
    name = "fixer"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        return {"fixer": {"note": "fixer stub: not implemented"}}
