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
integration ─► intake ─► (intake_mode?)
   GitHub/Jira/Plane          ├─ raw_to_spec ─► PM (raw → spec + tickets) ─► RFC? ─► [review gate] ─┐
                              ├─ spec_ready  ─► PM (spec → tickets)        ─► RFC? ─► [review gate] ─┤
                              └─ raw_to_dev  ──────────────────────────────────────────────────────────┤
                                                                                                       ▼
              worktree ─► Research ─► Coding ─► commit ─► PR (CODE) ─► Reviewer ─► Fixer ─► merge
                                                         (one PR per story, stories built sequentially)
```

- **Issue sources are pluggable integrations** — GitHub, Jira, or Plane — stored in the DB (secrets
  encrypted at rest) and resolved behind one `IssueProvider` interface. Add a source = add a provider.
  See **[docs/integrations.md](docs/integrations.md)** for the step-by-step "how to add an integration" guide.
- **Per-run intake mode** determines PM's role and what the build team receives:
  - `raw_to_spec` — PM receives raw requirements (issue text, uploaded docs) and produces a full
    spec + implementation tickets. A **human review gate** lets you approve before tickets are pushed
    to your task tool (Jira / Plane / file board).
  - `spec_ready` — The issue/document is already a specification. PM reads it and extracts
    implementation tickets (stories) without rewriting the spec from scratch. Same review gate applies.
  - `raw_to_dev` — PM is skipped entirely. The raw issue content is handed straight to the build
    team (Research → Coding → Review). Use when requirements are already unambiguous.
  A LangGraph **conditional edge** routes accordingly.
- **All six agents are real**, sharing the same `BaseAgent` contract and LangChain tool loop:
  - **PM** — raw requirements → epic + technical spec + implementation tickets (with traditional and
    LLM-assisted effort estimates); spec review gate before tickets are pushed to Jira/Plane/file board.
    Also runs **standalone in the PM workbench** (below).
  - **RFC** — optional pre-build design document (opt-in via `trigger: auto` in project config).
  - **Research** — code-intel spike; produces a grounded implementation plan per story.
  - **Coding** — reads the plan, writes/edits files in a git worktree, commits and opens a PR.
  - **Reviewer** — deep one-pass code review with severity-tagged findings; can auto-merge or gate.
  - **Fixer** — bounded fix loop; applies reviewer feedback and updates the PR.
  With no local clone configured, Research/Coding skip gracefully so PM-only runs still complete.
- **Per-story fan-out** — the story is the unit of execution. Each ticket from the spec becomes one
  `StoryState` built sequentially (one PR per story). Stories run in dependency order; failed stories
  can be retried from any step without re-running completed ones.
- **Story mode** — `single` (default): PM produces one story, one PR; `multiple`: PM decomposes the
  work, each story gets its own PR. Selectable at run-start; the review gate lets you uncheck stories
  in multiple mode.
- **PM workbench** (`/ui/pm`) — run PM **standalone** to generate a spec and stop (no auto-build),
  then iterate on story quality with a two-level feedback loop: a spec-level box regenerates the whole
  spec from your notes, and each story card has an inline **Refine** that re-elaborates just that
  ticket. When you're happy, one-click **Generate RFC** or **Build first story** kicks off that work
  (manual, never automatic). New PM run: `+ New` → *New PM run*, or `/ui/pm/new`.
- **Effort estimates** — each ticket carries a *traditional* estimate (without AI tooling) and an
  *LLM-assisted* estimate (typically 5–8× faster). The LLM proposes them and a deterministic Python
  repair pass normalizes the numbers (parses the text estimate → days; derives the LLM-assisted days
  via a configurable speedup factor when the model's are missing/inconsistent) — so estimates stay
  sane even on small-context models. Both are shown per-story and totalled. See
  [docs/plan/ash_architecture_and_plan.md §7 decision #29](docs/plan/ash_architecture_and_plan.md).
- **Specs go to the Board; code goes to the PR** (kept strictly separate).
- **Human-in-the-loop is a toggle** — `trigger: manual` pauses before any agent; `ApprovalGate`
  gates spec review and optional merge; each ticket runs in its own **git worktree**.
- The graph is a LangGraph `StateGraph` over one **namespaced** `WorkflowState`; runs are
  checkpointed in Postgres and addressable by `run_id`.

## UI & admin

- **Jinja2 UI at `/`** — dashboard, paginated runs list (`/ui/runs`), start-run form (pick
  integration + intake mode), live run status with pretty spec view and approve/reject gate.
- **PM workbench (`/ui/pm-workbench`)** — a direct list of standalone PM runs (rows open the
  workbench); the searchable spec archive lives at `/ui/pm-runs` ("All specs →").
- **LLM I/O (`/ui/runs/{id}/llm`)** — every agent↔LLM exchange (the exact prompt sent + response
  received) is persisted and shown per run, grouped by story/agent with token + model badges. Set
  `PERSIST_LLM_EXCHANGES=false` to disable capture.
- **Admin portal at `/admin`** (SQLAdmin) — CRUD for integrations (tokens encrypted via Fernet) and
  the run registry. Login uses DB-backed admin users (PBKDF2-hashed), with the `ADMIN_USER` /
  `ADMIN_PASSWORD` env user as a bootstrap fallback. Create admin users from the CLI:

  ```bash
  just create-admin alice          # prompts for the password (not echoed / not in shell history)
  # or: ash create-admin --username alice
  ```

## Architecture (src layout, single package)

```
src/ash/
├── api/            # FastAPI app + REST routes (POST /runs, GET /runs/{id}) + lifespan
├── web/            # Jinja2 server-rendered UI (dashboard, runs, PM runs, agents, approvals)
│                   #   SSE live run timeline · story cards · HITL approve/trigger gates
├── admin/          # SQLAdmin portal at /admin + DB-backed auth + Connector wizard
├── agents/         # BaseAgent (LangChain tool loop + structured output) +
│                   #   intake · pm · rfc · research · coding · reviewer · fixer
├── graph/          # WorkflowState (namespaced) · LangGraph builder · story fan-out ·
│                   #   Postgres checkpointer · Runner (start/resume/stop/retry)
├── integrations/   # IssueProvider + GitHub/Jira/Plane providers + MCP-over-HTTP loader
├── db/             # SQLAlchemy async · EncryptedString · models (Connector, RunRecord,
│                   #   SpecRecord, StoryRecord, AgentTask, AgentRunMetric, AgentPolicyRecord)
├── clients/        # boundary clients: async GitHub, git worktrees, gh PRs, board, code_intel
├── toolkits/       # LangChain BaseTool wrappers over clients (DevToolkit, board, codebase)
├── sinks/          # TaskSink abstraction + Jira/Plane/file-board sink implementations
├── llm/            # provider-agnostic chat-model factory (Anthropic / OpenAI-compatible)
├── config/         # pydantic-settings + projects/<name>.yaml loader (hybrid)
├── app_context.py  # composition root (build agents → graph → Runner)
└── cli.py          # thin local CLI (`ash list`, `ash run`)
projects/<name>.yaml # per-engagement config (repo, board, autonomy, budget, agent policies)
skills/<name>/SKILL.md
docs/{plan/,sources/}
tests/               # 173 pytest tests (mocked LLM/clients, MemorySaver checkpointer)
```

The tool layer is 3 levels: `clients/` (real logic) → `toolkits/` (`BaseTool` wrappers) → agents.
Orchestration follows **LangGraph-first**: all control flow is graph nodes + conditional edges +
`interrupt()`-based HITL gates — no bespoke asyncio loops or ad-hoc queues.

Spec-quality standards (the rules the PM agent follows + the org best practices vendored from
[arbisoft/ai-skillforge](https://github.com/arbisoft/ai-skillforge/tree/main/Claude)) live in
**[docs/best_practices.md](docs/best_practices.md)**.

## Quickstart

Requires **Python ≥ 3.12** and Docker (for Postgres and Chroma).

```bash
just setup                 # venv (py>=3.12) + editable install + dev tools
cp .env.example .env       # fill in credentials (see below)
just db-up                 # start Postgres (docker compose)
just serve                 # app at http://127.0.0.1:8000  (UI /, admin /admin, API /docs)
```

> **Setting this up for the first time / onboarding a teammate?** Follow the end-to-end runbook:
> **[docs/ONBOARD_A_PROJECT.md](docs/ONBOARD_A_PROJECT.md)** (env → infra → project YAML → connector
> → first run → troubleshooting; includes "do I need a fork?" — no, `mode: single` for your own repos).

Add an issue source in the **admin portal** (`/admin`), then start a run from the **UI** (`/ui/runs/new`)
or the API:

```bash
curl -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"project":"plane","item_id":"9213","integration_id":1,"intake_mode":"raw_to_spec"}'
curl localhost:8000/runs/<run_id>                   # -> status + per-agent state
```

`integration_id` is optional (omit it to use the legacy GitHub source from the project config);
`intake_mode` is one of `raw_to_spec` (default), `spec_ready`, `raw_to_dev`.

Or run once from the CLI (persists to Postgres, like the API):

```bash
just list plane            # list open issues from the project's source repo
just run plane 9213        # run the full graph once, print final state (persists to Postgres)
ash run --project plane --issue 9213 --ephemeral   # in-memory checkpointer (no Postgres)
```

### Configure `.env`

> Full reference (every variable, model resolution, working examples, troubleshooting):
> **[docs/configuration.md](docs/configuration.md)**.

- **LLM** (provider-agnostic): `LLM_PROVIDER=openai` + `LLM_BASE_URL` + `OPENAI_API_KEY` for an
  OpenAI-compatible gateway (LiteLLM/Ollama/vLLM), or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`
  for the native API. `LLM_MODEL` must be a model your key may use. Per-agent overrides via
  `AGENT_<NAME>__MODEL`.
- **Postgres**: `POSTGRES_DSN` (the checkpointer + app tables: integrations, run registry).
- **Secrets/admin**: `SECRET_KEY` (Fernet key — encrypts integration tokens at rest) and
  `ADMIN_USER` / `ADMIN_PASSWORD` (the `/admin` login). Generate a key:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
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

All six agents (PM, RFC, Research, Coding, Reviewer, Fixer) are production-grade implementations
sharing the same `BaseAgent` / LangChain tool loop. The full pipeline — from issue intake through
spec review, per-story fan-out, code generation, review, and fix — runs end-to-end.

**Effort estimates** — each PM ticket carries a *traditional* estimate (dev effort without AI) and
an *LLM-assisted* estimate. The LLM is instructed to apply a **5–8× speedup factor** reflecting
real-world productivity gains from AI pair-programming (e.g. traditional `3d` → LLM `0.5d`). Both
are in the same `Xd`/`Xw`/`Xh` unit so they can be compared directly. Numeric `estimate_days` and
`llm_estimate_days` fields enable per-run totals in the PM run detail page.

**Caveats:** code-generation grounding is still shallow — review generated PRs; don't trust them
blindly. Alembic migrations are not yet wired (schema changes use `ADD COLUMN IF NOT EXISTS`
backfills). See the plan for the full roadmap.
