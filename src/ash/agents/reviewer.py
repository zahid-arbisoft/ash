"""Reviewer agent — STUB. TODO (Phase 2): separate checker; quality/security/spec-compliance.

Must never be the same agent that wrote the code (maker/checker separation, plan §4a).
"""

from __future__ import annotations

from typing import Any

from ash.agents.base import BaseAgent
from ash.graph.state import WorkflowState


class ReviewerAgent(BaseAgent):
    name = "reviewer"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        return {"reviewer": {"note": "reviewer stub: not implemented"}}
