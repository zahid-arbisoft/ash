# AI-Driven PM + Engineering Multi-Agent Boilerplate — Design Spec

**Date:** 2026-06-10
**Status:** Approved for planning
**Owner:** abdul.basit02@arbisoft.com

---

## 1. Goal & Scope

Deliver a **structural skeleton plus one real vertical slice** of an AI-driven
PM + Engineering multi-agent system, as an executable foundation a team fills in.

- The full four-agent graph (**PM → Dev → Reviewer → Fixer → Merge**) is wired
  and runs end-to-end via LangGraph.
- **Only the PM agent is real**: it reads a GitHub Issue, produces a technical
  spec via an LLM, and posts that spec back as a comment on the issue.
- **Dev, Reviewer, and Fixer are pass-through stubs**: they annotate their
  namespaced state and advance the graph, doing no real work yet.
- Everything not required by the PM slice ships as a **uniform stub behind a
  clear extension point** (same patterns, `NotImplementedError` + `TODO`).

The deliverable is a repository that satisfies the Definition of Done in §12.

### Explicit non-goals (deferred, with documented seams)
- Real Dev/Reviewer/Fixer logic, Git operations, and PR APIs.
- Job queue / dedicated worker process (Redis/Celery/RQ).
- Object storage (S3/GCS) usage.
- Prometheus/Grafana metrics and ELK/OpenSearch log aggregation.
- Multiple board providers beyond GitHub Issues (Jira, Linear, ClickUp, Azure DevOps).
- Authentication/authorization on the API.

---

## 2. Technology Decisions (locked)

| Area | Decision |
|------|----------|
| Language | Python 3.11+ |
| Orchestration | LangGraph (stateful graph, Postgres checkpointer) |
| LLM framework | LangChain |
| Agent model | `BaseAgent` abstract class; each agent registered as a graph node |
| State | Single root `WorkflowState` with **namespaced sub-states** per agent |
| Checkpointer | **Postgres** from day one (`thread_id` keyed per run / board-item) |
| LLM provider | Provider-agnostic factory; default **Anthropic `claude-sonnet-4-6`**; per-agent overrides with global fallback |
| Tools | 3 layers — `clients/` (plain callables) → `toolkits/` (`@tool` wrappers) → agents bind toolkits |
| Vector store | **Chroma** (docker-compose service via `HttpClient`, persistent volume) |
| Relational DB | **Postgres** (checkpointer + app tables) |
| Object storage | **Stubbed** client (S3/GCS) |
| Cache/queue | **Stubbed** client (Redis) |
| Entrypoint | **FastAPI**, fully async |
| Execution | `POST /runs` returns `run_id`, runs graph as **background task**; status via Postgres checkpointer |
| Scheduling | **APScheduler** in-process board-scan job calling the same internal runner |
| Config | `pydantic-settings`, `.env`-driven, no hardcoded secrets |
| Logging | **structlog** JSON, `run_id` / `thread_id` bound to every line |
| Tracing | **Langfuse** LangChain callback handler, env-gated (no-op when unset) |
| Package manager | **uv** + `pyproject.toml` |
| Lint + format | **Ruff** |
| Type checking | **mypy** strict, enforced in CI |
| Testing | **pytest + pytest-asyncio**; LLM and clients mocked (deterministic, offline) |
| Pre-commit | ruff + mypy + quick test subset |
| CI | **GitHub Actions**: ruff → mypy → pytest on PRs |
| Layout | `src/` layout, single installable package `agentsys/` |

---

## 3. Repository Layout

```
specs-for-agent/                 # repo root
├── src/agentsys/
│   ├── __init__.py
│   ├── config/                  # Pydantic settings
│   │   ├── __init__.py
│   │   └── settings.py          # Settings, per-agent model config, env loading
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseAgent abstract class
│   │   ├── pm.py                # PMAgent — REAL
│   │   ├── dev.py               # DevAgent — STUB (pass-through)
│   │   ├── reviewer.py          # ReviewerAgent — STUB
│   │   └── fixer.py             # FixerAgent — STUB
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py             # WorkflowState + namespaced sub-states
│   │   ├── nodes.py             # node adapters wrapping each agent
│   │   ├── builder.py           # graph construction + edges
│   │   ├── checkpointer.py      # Postgres checkpointer factory
│   │   └── runner.py            # internal run/resume API (shared by API + scheduler)
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── github.py            # GitHubClient — REAL (read issue, post comment)
│   │   ├── chroma.py            # VectorStoreClient over Chroma HttpClient — REAL
│   │   ├── postgres.py          # connection/pool helpers — REAL
│   │   ├── object_storage.py    # S3/GCS — STUB
│   │   └── redis.py             # Redis — STUB
│   ├── toolkits/
│   │   ├── __init__.py
│   │   ├── base.py              # toolkit protocol: get_tools() -> list[BaseTool]
│   │   ├── board.py             # BoardToolkit — REAL (@tool wrappers over GitHubClient)
│   │   ├── git.py               # STUB
│   │   ├── pr.py                # STUB
│   │   ├── codebase.py          # STUB (search)
│   │   ├── shell.py             # STUB (run commands)
│   │   └── messaging.py         # STUB (Slack/Teams)
│   ├── llm/
│   │   ├── __init__.py
│   │   └── factory.py           # get_chat_model(agent_settings) -> BaseChatModel
│   ├── api/
│   │   ├── __init__.py
│   │   ├── app.py               # FastAPI app factory + lifespan
│   │   ├── routes.py            # POST /runs, GET /runs/{run_id}
│   │   └── schemas.py           # request/response Pydantic models
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── board_scan.py        # APScheduler job -> runner
│   └── observability/
│       ├── __init__.py
│       ├── logging.py           # structlog config
│       └── langfuse.py          # env-gated callback handler factory
├── tests/
│   ├── conftest.py              # fixtures: mocked LLM, mocked GitHubClient, test settings
│   ├── agents/
│   ├── graph/
│   ├── clients/
│   ├── toolkits/
│   └── api/
├── docker-compose.yml           # Postgres + Chroma
├── .env.example
├── pyproject.toml               # uv, ruff, mypy, pytest config
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml
└── README.md
```

---

## 4. Agent Abstraction

```python
class BaseAgent(ABC):
    name: str                                   # "pm" | "dev" | "reviewer" | "fixer"

    def __init__(self, settings: Settings) -> None: ...

    @abstractmethod
    async def run(self, state: WorkflowState) -> dict:
        """Return a partial state update (the agent's namespaced sub-state)."""

    def get_model(self) -> BaseChatModel:        # via factory, per-agent override + global fallback
        ...

    def get_tools(self) -> list[BaseTool]:       # default: []
        ...

    def get_prompt(self) -> ChatPromptTemplate:  # agent-specific system/instructions
        ...
```

- **Contract:** an agent reads the root `WorkflowState`, does its work, returns a
  partial update scoped to its own namespace. Nodes never mutate another agent's
  namespace.
- **Testability:** each agent is constructed with `Settings` and exercised in
  isolation with a mocked model and mocked toolkits.
- **Node adapter** (`graph/nodes.py`) wraps `agent.run` to integrate with LangGraph,
  binds the Langfuse callback (when enabled), and applies error handling (§9).

### Agent responsibilities
- **PMAgent (real):** read the target board item via `BoardToolkit`, prompt the model
  to produce a structured technical spec (Pydantic output parser), persist the spec in
  the `pm` sub-state, and post it back as a comment via `BoardToolkit`.
- **DevAgent / ReviewerAgent / FixerAgent (stubs):** write a placeholder marker into
  their namespaced sub-state, log a `TODO`, and advance. No external calls.

---

## 5. Orchestration & State

### State shape (namespaced sub-states under one root)

```python
class PMState(BaseModel):
    spec: str | None = None
    comment_url: str | None = None
    error: str | None = None

class DevState(BaseModel):
    note: str | None = None
    error: str | None = None

# ReviewerState, FixerState: analogous

class WorkflowState(BaseModel):
    run_id: str
    board: str                  # "github"
    item_id: str
    pm: PMState = PMState()
    dev: DevState = DevState()
    reviewer: ReviewerState = ReviewerState()
    fixer: FixerState = FixerState()
    status: Literal["running", "completed", "failed"] = "running"
```

> Note: LangGraph state is typically a `TypedDict` with reducer annotations; the
> Pydantic models above define the shape. The plan phase will choose the concrete
> LangGraph state representation (`TypedDict` with nested models vs Pydantic state)
> consistent with the installed LangGraph version. Sub-state namespacing is the
> invariant; the container type is an implementation detail.

### Graph

```
START → pm → dev → reviewer → fixer → merge → END
```

- Linear edges for the boilerplate (no dynamic router).
- **Postgres checkpointer** persists state per `thread_id` (= `run_id` / board-item).
- `merge` is a terminal node that sets `status = "completed"` (or `"failed"` if any
  sub-state carries an error, per §9).

### Runner (`graph/runner.py`)
- `async def start_run(board, item_id) -> run_id`: create `thread_id`, kick off the
  compiled graph with the Postgres checkpointer and Langfuse callbacks.
- `async def get_run(run_id) -> RunStatus`: read latest checkpointed state.
- **Single shared entry** used by both the FastAPI background task and the
  APScheduler job. The queue/worker swap-in point lives here.

---

## 6. Tools — Three Layers (option C)

1. **`clients/`** — plain async callables with the real API/auth/HTTP/DB logic.
   Independently testable and mockable. `GitHubClient.get_issue(item_id)` and
   `GitHubClient.post_comment(item_id, body)` are real; others stubbed.
2. **`toolkits/`** — `@tool`-decorated thin wrappers exposing `BaseTool`s with
   carefully written **names, descriptions, and arg schemas** (the only thing the
   model uses to decide tool usage). `BoardToolkit.get_tools()` returns the read/comment
   tools; stub toolkits raise `NotImplementedError` with a `TODO`.
3. **Agents** bind only the toolkits they need via `get_tools()`.

**Real for the slice:** `GitHubClient` + `BoardToolkit` (`read_board_item`, `post_board_comment`).
**Stubbed (same pattern):** git, PR APIs, codebase search, shell/run-commands, messaging (Slack/Teams).

---

## 7. Data Layer

- **Postgres (real):** LangGraph checkpointer + any app tables. Provisioned in
  `docker-compose.yml`; connection via `pydantic-settings`.
- **Chroma (real):** docker-compose service accessed through `chromadb.HttpClient`,
  wrapped by `VectorStoreClient`, persistent volume. Not exercised by the PM slice
  yet, but ready and running so retrieval is turnkey for the next phase.
- **Object storage (stub):** `ObjectStorageClient` interface, `NotImplementedError`.
- **Redis (stub):** `RedisClient` interface, `NotImplementedError`.

---

## 8. Entry, Execution & Scheduling

### FastAPI (async)
- `POST /runs` `{ "board": "github", "item_id": "42" }` → starts the graph as a
  **background task** (`asyncio` task / FastAPI `BackgroundTasks`), returns
  `{ "run_id": "..." }` immediately (HTTP 202).
- `GET /runs/{run_id}` → reads status/state from the Postgres checkpointer:
  `{ "run_id", "status", "pm": {...}, ... }`.
- App lifespan initializes: settings, structlog, Postgres pool, Chroma client,
  compiled graph, APScheduler.

### Scheduling
- **APScheduler** in-process job periodically scans the configured board for
  new/changed items and calls `runner.start_run(...)` — the **same internal runner**
  the API uses (never via HTTP). Interval is config-driven; disabled by default in
  tests.

### Deferred seam
- Job queue / dedicated worker (Redis/Celery). The `runner` abstraction is the swap-in
  point: replace the background-task launch with an enqueue call without touching the
  API or graph.

---

## 9. Config, Observability & Error Handling

### Config
- `pydantic-settings` `Settings` loaded from environment / `.env`. Includes: LLM
  provider + model, **per-agent model overrides** (with global fallback), GitHub token,
  Postgres DSN, Chroma host/port, APScheduler interval, Langfuse keys/host, log level.
- `.env.example` enumerates every key. No secrets committed.

### Logging
- **structlog** JSON renderer to stdout (12-factor). A context processor binds
  `run_id` and `thread_id` to every log line within a run.

### Tracing
- **Langfuse** LangChain `CallbackHandler` created by an env-gated factory
  (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`). When keys are
  unset the factory returns `None` and tracing is a no-op. The handler is attached to
  the graph run config's `callbacks`.

### Error handling
- Each node wraps `agent.run` in try/except. On failure it writes the error message
  into that agent's namespaced sub-state, logs + traces it, and advances toward a
  terminal state (the run is marked `failed` at `merge` if any sub-state carries an
  error) rather than crashing the process.
- Deferred observability (Prometheus `/metrics`, ELK) exists only as documented seams.

---

## 10. Testing Strategy

- **pytest + pytest-asyncio.** All tests deterministic and offline.
- **LLM mocked** via a fake `BaseChatModel` returning canned structured output.
- **Clients mocked** (`GitHubClient`, Chroma, Postgres) at the boundary; the 3-layer
  tool design makes this clean.
- Coverage targets for the boilerplate:
  - `PMAgent.run` produces the expected spec + posts a comment (mocked client).
  - Stub agents advance state without external calls.
  - Graph traverses `pm→dev→reviewer→fixer→merge`, persists, and reports `completed`.
  - Error in a node marks the run `failed` and does not crash.
  - `POST /runs` returns a `run_id`; `GET /runs/{id}` reflects checkpointer state.
  - LLM factory honors per-agent override + global fallback.
  - Langfuse factory returns `None` when env unset.

---

## 11. Toolchain & Quality Gates

- **uv** for dependency management and locking via `pyproject.toml`.
- **Ruff** for lint + format.
- **mypy** strict, enforced in CI.
- **pre-commit**: ruff (lint+format) + mypy + a quick test subset.
- **GitHub Actions** `ci.yml`: `uv sync` → ruff → mypy → pytest on every PR.
- **`src/` layout**, single installable package `agentsys`.

---

## 12. Definition of Done

A developer can:

1. `git clone` the repo.
2. `cp .env.example .env` and add a GitHub token + Anthropic API key.
3. `docker compose up` (Postgres + Chroma come up healthy).
4. `uv sync` and run the API (`uv run uvicorn agentsys.api.app:app` or documented command).
5. `POST /runs { "board": "github", "item_id": "<issue>" }` → receives a `run_id`.
6. The PM agent reads the issue, generates a technical spec, and **posts it as a
   comment** on the issue.
7. The graph traverses all four nodes (PM real, others stubbed) and reaches `merge`.
8. `GET /runs/{run_id}` reports `completed` with the PM sub-state populated.
9. The APScheduler scan triggers the same path automatically on its interval.
10. `ruff`, `mypy --strict`, and `pytest` all pass locally and in CI.

Langfuse tracing is visible when keys are configured; structlog emits JSON with
`run_id`/`thread_id` on every line.

---

## 13. Build Sequence (high level — detailed in the plan)

1. Project scaffolding: `uv`, `pyproject.toml`, ruff/mypy/pytest config, `src/` layout, CI, pre-commit.
2. Config + observability foundations (settings, structlog, Langfuse factory).
3. Clients layer: real `GitHubClient`, `PostgresPool`, `VectorStoreClient` (Chroma); stub clients.
4. LLM factory (provider-agnostic, per-agent override).
5. Toolkits: real `BoardToolkit`; stub toolkits.
6. `BaseAgent` + PMAgent (real) + stub agents.
7. Graph: state, nodes, checkpointer, builder, runner.
8. FastAPI app + routes + background execution.
9. APScheduler board-scan job.
10. docker-compose (Postgres + Chroma), `.env.example`, README.
11. Tests across all layers; green CI.
