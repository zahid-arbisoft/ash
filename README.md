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
   GitHub/Jira/Plane          ├─ raw_to_spec ─► PM (raw → spec + tickets) ─► [review gate] ─┐
                              ├─ spec_ready  ─► PM (spec → tickets)        ─► [review gate] ─┤
                              └─ raw_to_dev  ─────────────────────────────────────────────────┤
                                                                                              ▼
              worktree ─► Research ─► Coding ─► commit ─► PR (CODE) ─► Reviewer ─► Fixer ─► merge
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
- **PM/Research/Coding are real; Reviewer/Fixer are stubs** behind the same `BaseAgent` contract.
  With no local clone configured, Research/Coding skip gracefully so a PM-only run still completes.
- **Specs go to the Board; code goes to the PR** (kept strictly separate).
- **Human-in-the-loop is a toggle** (`ApprovalGate`); each ticket runs in its own **git worktree**.
- The graph is a LangGraph `StateGraph` over one **namespaced** `WorkflowState`; runs are
  checkpointed in Postgres and addressable by `run_id`.

## UI & admin

- **Jinja2 UI at `/`** — dashboard, paginated runs list (`/ui/runs`), start-run form (pick
  integration + intake mode), live run status with pretty spec view and approve/reject gate.
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
├── api/            # FastAPI app + routes (POST /runs, GET /runs/{id}) + lifespan
├── web/            # Jinja2 server-rendered UI (dashboard, integrations, start/track runs)
├── admin/          # SQLAdmin portal at /admin + auth backend
├── agents/         # BaseAgent + intake + pm/research/coding (real) + reviewer/fixer (stubs)
├── graph/          # state (namespaced), nodes, checkpointer, builder (conditional), runner
├── integrations/   # IssueProvider abstraction + GitHub/Jira/Plane providers + registry/service
├── db/             # SQLAlchemy async (base/session), EncryptedString, models (Integration, RunRecord)
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
just serve                 # app at http://127.0.0.1:8000  (UI /, admin /admin, API /docs)
```

Add an issue source in the **admin portal** (`/admin`), then start a run from the **UI** (`/ui/runs/new`)
or the API:

```bash
curl -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"project":"plane","item_id":"9213","integration_id":1,"intake_mode":"raw_to_spec"}'
curl localhost:8000/runs/<run_id>                   # -> status + per-agent state
```

`integration_id` is optional (omit it to use the legacy GitHub source from the project config);
`intake_mode` is one of `raw_to_spec` (default), `spec_ready`, `raw_to_dev`.

Or run once locally without Postgres (in-memory checkpointer):

```bash
just list plane            # list open issues from the project's source repo
just run plane 9213        # run the full graph once, print final state
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

Issue-source integrations (GitHub/Jira/Plane), per-run intake routing, the FastAPI surface, the
Jinja2 UI, and the SQLAdmin portal are in place; orchestration is LangGraph with a Postgres
checkpointer. PM (spec → Board) is real; Research/Coding produce a grounded plan and a draft fork PR
when a clone is configured; Reviewer/Fixer are stubs. **Caveat:** code-generation grounding is still
shallow — review generated PRs; don't trust them blindly. Next: real Reviewer (maker/checker
separation), the bounded Fixer loop, Alembic migrations, then the scheduled heartbeat. See the plan
for the full roadmap.
