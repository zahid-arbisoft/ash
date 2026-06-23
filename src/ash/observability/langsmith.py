"""LangSmith observability — automatic LLM tracing + manual scoring.

LangChain auto-traces every .ainvoke() / .invoke() call to LangSmith when these env vars
are set (no code changes needed):
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=lsv2_...
    LANGCHAIN_PROJECT=ash          # optional; groups runs in the LangSmith UI

This module adds a thin scoring layer on top: attach numeric feedback scores to the
run that triggered the event (HITL decision, test outcome, reviewer verdict). Scores
appear in LangSmith under the run → Feedback tab.

The `run_id` used by ASH graph nodes is passed through LangChain as the `run_id` config
key so LangSmith records them under the same ID, making lookups deterministic.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None
_init_attempted: bool = False


def _get_client() -> Any | None:
    """Lazy singleton LangSmith client. Returns None if tracing is disabled or unavailable."""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    try:
        import os

        if os.environ.get("LANGCHAIN_TRACING_V2", "").lower() not in ("true", "1"):
            return None
        from langsmith import Client

        _client = Client()
        logger.info("[langsmith] client initialised")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langsmith] client init skipped: %s", exc)
    return _client


def score(
    run_id: str,
    name: str,
    value: float,
    *,
    comment: str = "",
) -> None:
    """Attach a feedback score to a LangSmith run. Fire-and-forget — never raises.

    Scores surface in LangSmith under the run → Feedback tab and in dataset evals.

    Common names used by ASH:
      "hitl_decision"    1.0 = approved, -1.0 = rejected
      "tests_passed"     1.0 = green,     0.0 = failing
      "reviewer_verdict" 1.0 = approve,   0.0 = request_changes
    """
    client = _get_client()
    if client is None:
        return
    try:
        import uuid

        client.create_feedback(
            run_id=uuid.UUID(run_id) if len(run_id) == 36 else run_id,
            key=name,
            score=value,
            comment=comment or None,
        )
        logger.debug("[langsmith] feedback %s=%.2f on run %s", name, value, run_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langsmith] score() failed: %s", exc)
