"""ApprovalGate — the single place human-in-the-loop logic lives (plan §7b).

Autonomy is one flag away: flip the per-gate config and the same code path runs unattended.
No `if human:` checks scattered across the pipeline.
"""

from __future__ import annotations

from enum import Enum

from .config import Autonomy


class Decision(str, Enum):
    auto_approve = "auto_approve"
    wait_for_human = "wait_for_human"


class ApprovalGate:
    """kind -> whether a human must approve. Driven entirely by project autonomy config."""

    def __init__(self, autonomy: Autonomy):
        self._require = {
            "merge": autonomy.require_human_for_merge,
            "escalation": autonomy.require_human_for_escalation,
        }

    def check(self, kind: str) -> Decision:
        if self._require.get(kind, True):
            return Decision.wait_for_human
        return Decision.auto_approve

    def requires_human(self, kind: str) -> bool:
        return self.check(kind) is Decision.wait_for_human
