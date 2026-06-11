# Agentic Software House (ASH)

A platform that behaves like a **software house with effectively unlimited staff** — except the
staff are **agents** (PM, Research, Coding, Reviewer, Fixer) and the **clients** are humans who
provide requirements, choose integrations, define the loop flow, and keep oversight via feedback
gates. Built as a reusable, config-driven **loop engine** orchestrated with **LangGraph** and served
over **FastAPI**, with per-run state persisted in a **Postgres checkpointer**.

> Design & rationale live in [`docs/plan/ash_architecture_and_plan.md`](docs/plan/ash_architecture_and_plan.md)
> (the authoritative plan). Source specs are under [`docs/sources/`](docs/sources/). This README is
> the quickstart.

## How it works

```
issue ─► PM spec ─► Board (specs/tickets: .md/.json today, Jira/Plane/Trello later)
                └─► worktree ─► Research ─► Coding ─► commit ─► PR (CODE) ─► Reviewer ─► Fixer ─► merge
```

- **PM/Research/Coding are real; Reviewer/Fixer are stubs** behind the same `BaseAgent` contract
  (filled in Phases 2–3). With no local clone configured, Research/Coding skip gracefully so a
  PM-only run still completes (reads issue → produces spec → publishes to the Board).
- **Specs go to the Board; code goes to the PR** (kept strictly separate).
- **Human-in-the-loop is a toggle** (`ApprovalGate`) — one flag away from autonomous.
- Each ticket runs in its own **git worktree** for parallel safety.
- The graph is a LangGraph `StateGraph` over one **namespaced** `WorkflowState`; runs are
  checkpointed in Postgres and addressable by `run_id`.

## Architecture (src layout, single package)

```
src/ash/
├── api/            # FastAPI app + routes (POST /runs, GET /runs/{id}) + lifespan
├── agents/         # BaseAgent + pm/research/coding (real) + reviewer/fixer (stubs)
├── graph/          # state (namespaced), nodes, checkpointer, builder, runner
├── clients/        # boundary clients: async GitHub, git worktrees, gh PRs, board sink, code_intel
├── toolkits/       # LangChain @tool wrappers over clients (board, codebase)
├── llm/            # provider-agnostic chat-model factory (Anthropic / OpenAI-compatible)
├── config/         # pydantic-settings + projects/<name>.yaml loader (hybrid)
├── app_context.py  # composition root (build agents → graph → Runner)
└── cli.py          # thin local CLI (`ash list`, `ash run`)
projects/<name>.yaml # per-client/engagement config (repo, board, autonomy, budget)
skills/<name>/SKILL.md
docs/{plan/,sources/}
tests/               # pytest + pytest-asyncio (mocked LLM/clients, MemorySaver)
```

The tool layer is 3 levels: `clients/` (real logic) → `toolkits/` (`BaseTool` wrappers) → agents.

## Quickstart

Requires **Python ≥ 3.12** and Docker (for Postgres).

```bash
just setup                 # venv (py>=3.12) + editable install + dev tools
cp .env.example .env       # fill in credentials (see below)
just db-up                 # start Postgres (docker compose)
just serve                 # FastAPI at http://127.0.0.1:8000/docs
```

Start a run:

```bash
curl -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"project":"plane","item_id":"9213"}'        # -> {"run_id":"..."}
curl localhost:8000/runs/<run_id>                   # -> status + per-agent state
```

Or run once locally without Postgres (in-memory checkpointer):

```bash
just list plane            # list open issues from the project's source repo
just run plane 9213        # run the full graph once, print final state
```

### Configure `.env`
- **LLM** (provider-agnostic): `LLM__PROVIDER=openai` + `LLM_BASE_URL` for an OpenAI-compatible
  gateway (LiteLLM/Ollama/vLLM), or `anthropic` for the native API. Per-agent overrides via
  `AGENT_<NAME>__MODEL`.
- **Postgres**: `POSTGRES_DSN` (the checkpointer / run state of record).
- **Git**: the engine pushes over **HTTPS via `gh`** (`gh auth setup-git`). Set `LOCAL_REPO_PATH`
  (or `work.local_repo_path`) to an existing clone of the work-target repo to enable Research/Coding.

## Quality gates

```bash
just lint        # ruff
just typecheck   # mypy --strict
just test        # pytest
just check       # all three (what CI runs)
```

## Status

PM (spec → Board) is real; Research/Coding produce a grounded plan and a draft fork PR when a clone
is configured. Reviewer/Fixer are stubs. Orchestration (LangGraph), checkpointing (Postgres), and
the FastAPI surface are in place. **Caveat:** code-generation grounding is still shallow — review
generated PRs; don't trust them blindly. Next: real Reviewer (maker/checker separation), the bounded
Fixer loop, then the scheduled heartbeat. See the plan for the full roadmap.
