"""Shared fixtures for agent unit tests.

These tests call ``agent.run()`` directly (not through the LangGraph runtime), so the manual-trigger
gate's ``interrupt()`` would raise "Called get_config outside of a runnable context". Since the
default trigger is now ``manual`` for every agent except PM (decision: PM auto, rest manual), we
simulate the human clicking *Trigger* by making ``interrupt`` return ``"run"`` — the gate then
proceeds, letting each test exercise the agent's core logic. The gate/interrupt behaviour itself is
covered at the graph level (tests/graph/test_resume.py) and for PM in test_pm.py (which patches its
own ``ash.agents.pm.interrupt``).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auto_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ash.agents.base.interrupt", lambda _payload=None: "run")
