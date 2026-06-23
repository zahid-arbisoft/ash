# LangSmith Observability

ASH uses [LangSmith](https://smith.langchain.com) for LLM tracing and evaluation scoring.
LangChain has native LangSmith support — tracing is **zero code**, just env vars.

---

## Quick start

1. Sign up at https://smith.langchain.com (free)
2. Create a project (e.g. `ash`)
3. Copy your API key from Settings → API Keys
4. Add to `.env`:

```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_...
LANGCHAIN_PROJECT=ash
```

5. Restart ASH — every LLM call is now traced automatically.

---

## What gets traced

| Signal | LangSmith concept | When |
|---|---|---|
| Every LangChain LLM call | Run (child) inside a trace | Automatic — no code needed |
| Human Approve at HITL gate | Feedback `hitl_decision` = +1.0 | `web/routes.py` |
| Human Reject at HITL gate | Feedback `hitl_decision` = −1.0 | `web/routes.py` |
| Coding agent test suite result | Feedback `tests_passed` = 1/0 | `agents/coding.py` |
| Reviewer verdict | Feedback `reviewer_verdict` = 1/0 | `agents/reviewer.py` |

Feedback scores appear under each run's **Feedback** tab in the LangSmith UI.

---

## Environment variables

| Variable | Description |
|---|---|
| `LANGCHAIN_TRACING_V2` | Set to `true` to enable tracing |
| `LANGCHAIN_API_KEY` | API key from smith.langchain.com |
| `LANGCHAIN_PROJECT` | Project name in LangSmith UI (default: `default`) |

When `LANGCHAIN_TRACING_V2` is not set or `false`, tracing is off and the scoring wrapper
is a no-op — zero overhead.

---

## Adding more feedback scores

Use `ash.observability.langsmith.score()` anywhere in agent code:

```python
from ash.observability import langsmith as _ls

_ls.score(
    state.run_id,
    "my_metric",
    value,           # float
    comment="...",   # optional
)
```

The call is fire-and-forget and silently no-ops when tracing is disabled.

---

## Architecture

LangChain reads `LANGCHAIN_TRACING_V2` + `LANGCHAIN_API_KEY` at import time and installs
a global tracer. No per-call changes are needed in ASH agent code.

`src/ash/observability/langsmith.py` is a thin wrapper around `langsmith.Client` for the
feedback scoring API only. The `langsmith` package is already installed as a LangChain
transitive dependency — no extra pip install needed.
