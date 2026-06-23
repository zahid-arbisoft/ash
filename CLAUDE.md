# Standing instructions (read me at the start of every session)

This file is my own working memory for this project. If I forget how we work, I re-read this and
the plan before doing anything else.

## Source of truth
- The plan is **`docs/plan/ash_architecture_and_plan.md`**. It is authoritative for architecture
  decisions. The original source specs live in **`docs/sources/`**. Do NOT modify docs — they are
  read-only references.
- **Every decision and every implementation change is recorded in the plan**, and appended to its
  **Changelog (§11)**. The plan never lags the code. Do this in the same turn as the change — don't
  defer it.
- New decisions also get a row in the plan's "Locked Decisions" table (§7) when they're a choice,
  not just code.

## OpenSpec (spec-driven development)
We use **[OpenSpec](https://openspec.dev/)** (`openspec/`) as the behavioral spec layer going forward.
- **`openspec/config.yaml`** — project context and spec rules.
- **`openspec/specs/<domain>/spec.md`** — behavioral source of truth per domain (observable
  requirements + scenarios). These mirror the plan but focus on *what* not *how*.
- **`openspec/changes/<name>/`** — one folder per in-progress change: `proposal.md`, `design.md`,
  `tasks.md`, and delta `specs/`. Use `/opsx:propose "idea"` to scaffold a new change.
- **Workflow:** `/opsx:propose` → implement via `/opsx:apply` → `/opsx:archive` merges delta specs.
- The original `docs/plan/` files are never modified — they remain the architectural reference.
  OpenSpec specs are the behavioral (requirement) reference.

## What we're building (one line)
A **multi-tenant agentic "software house"** (plan §0): agents are the staff (PM/Research/Dev/QA/
Docs/Reviewer/Fixer), humans are **clients** who set requirements/integrations/flow and keep
oversight. Built as a reusable, config-driven loop engine wrapped in a self-feeding loop. Plane is
client/target #1; SaaS packaging is a later layer that must not change the agent loop.

## Non-negotiable design rules
1. **Engine is generic; projects are data.** No hardcoded repo names in `src/`. Per-project config
   lives in `projects/<name>.yaml`, skills in `skills/<name>/SKILL.md`, state in `runtime/<name>/`.
2. **Human-in-the-loop is a toggle** (`ApprovalGate`), one flag away from autonomous. Never scatter
   `if human:` checks.
3. **Triggers (inputs) and sinks (outputs) are pluggable connectors**, selected in config — not code
   changes. Don't hardwire "GitHub issue in, JSON out".
3b. **Specs → Board, code → PR** (§4c). Specs/tickets publish to a Board sink (file today;
   Jira/Plane/Trello later) for client oversight; PRs carry **implementation only**.
4. **Repo topology is per-project**: `fork` / `single` / `closed-source(private)`.
5. **Sequencing discipline:** do NOT build triggers/sinks/UI until the core loop (Phases 0–3) is
   trustworthy. Keep human verification gates until the layer below has earned removal.
7. **Admin Portal**: When creating a new database model, always register it in the admin portal (`src/ash/admin/views.py` and `src/ash/admin/__init__.py`).

## LangChain/LangGraph-first — religiously.
Prefer maintained LangChain ecosystem primitives over
   hand-rolled code, and model **all orchestration/control-flow with LangGraph**: graph state +
   reducers, nodes, conditional edges, subgraphs, `Send`/map-reduce, and **checkpointer-based
   interrupts/resume** — not bespoke loops, queues, or `if/while` flow in Python. When a requirement
   doesn't fit the current graph shape, **restructure the graph** rather than bolting control flow on
   the side. Canonical example: per-story fan-out is a `stories[ticket_id]` reducer + a
   `story_router`→`story_build` subgraph loop (decision #26 /
   `docs/plan/per_story_fanout_and_oversight_plan.md`), not a Python for-loop over tickets.

## Environment notes
- LLM is provider-agnostic via a **LangChain** factory. For a **LiteLLM gateway** (`/v1`) use
  `LLM_PROVIDER=openai` + `LLM_BASE_URL=...` (NOT `anthropic`). Per-agent overrides via
  `AGENT_<NAME>__MODEL`. Agents force structure via `.with_structured_output`.
- `gh` CLI is authenticated as `zahid-arbisoft`. **Git auth = HTTPS via gh** (`gh auth setup-git`);
  the engine fetches/pushes over the HTTPS URL, NOT the clone's SSH origin (no ssh-agent headless).
- Local clone via `LOCAL_REPO_PATH` (origin=fork). Without it, Research/Coding **skip gracefully**
  so a PM-only run still completes.
- **Python ≥3.12** (target 3.12; 3.13 also fine). `.venv` is built with `python3.13` here since 3.12
  isn't installed locally and the old 3.14 venv can't take the langchain/langgraph wheels. Engine is
  editable-installed (`pip install -e .[dev]`). Commands live in the `justfile` (`just --list`).
- **Architecture:** single package `src/ash/` (no Django). Entry = **FastAPI** (`src/ash/api`,
  `POST /runs` / `GET /runs/{id}`); orchestration = **LangGraph** (`src/ash/graph`, conditional
  intake routing); run state = **Postgres checkpointer** (AsyncPostgresSaver). App DB = **SQLAlchemy
  async** (`src/ash/db`) for integrations + run registry; secrets **encrypted at rest** (Fernet via
  `db/crypto.EncryptedString`, key = `SECRET_KEY`). Issue sources = pluggable **integrations**
  (`src/ash/integrations`: GitHub/Jira/Plane behind `IssueProvider`). FE = **Jinja2** at `/`
  (`src/ash/web`); admin = **SQLAdmin** at `/admin` (`src/ash/admin`, env creds). Per-run
  `intake_mode` (`raw_to_spec`/`spec_ready`/`raw_to_dev`) routes use/skip PM. Tools are 3-layer:
  `clients/` → `toolkits/` → agents. Config is hybrid: `pydantic-settings` + `projects/<name>.yaml`.
  Quality gates: ruff + **mypy --strict** + pytest, enforced in CI + pre-commit.

## Current status
- **Re-architected (2026-06-11)** to FastAPI + async + LangGraph + Postgres + LangChain; added
  **integrations + admin + UI** (decision #19), then **PM agent v2** (2026-06-12).
- **PM v2 + HITL review gate (2026-06-15, decision #20):** PM is now two graph nodes — `pm`
  (generate spec / extract tickets → board write → checkpoint) and `pm_publish` (calls
  `langgraph.types.interrupt` → pauses → user reviews spec in UI → Approve/Reject → tickets pushed
  to connector). `raw_to_spec` and `spec_ready` both route through PM; `raw_to_dev` skips PM.
  `spec_ready` uses a distinct prompt ("extract tickets from pre-written spec") — the old brittle
  JSON-parsing shortcut removed. Pretty tabbed spec view on run status page. Paginated `/ui/runs`.
- **Connectors (unified):** one `Connector` model/table (`db/models.py`) replaces the old
  `Integration`+`TaskSink`; `is_source`/`is_sink`/`is_default_sink` toggles let one row (e.g. Jira)
  be both source and sink. Single `ConnectorAdmin` at `/admin`; UI at `/ui/connectors`. Run fields
  `integration_id` (source) / `task_sink_id` (sink) now reference connector ids.
- **Agent runtime (`docs/plan/agent_runtime_and_connectors_plan.md`):** **P0+P1 done** — all
  structured agents run on LangChain **`create_agent`** via `BaseAgent.build_agent()`/`generate()`
  (langchain 1.3.8 + langchain-mcp-adapters 0.3.0). **P4 mechanism done** — `Runner.resume_run` +
  `POST /runs/{id}/resume` + interrupt/resume-through-checkpointer test. **P3 MCP loader done**
  (hosted HTTP): `Connector.transport="http"` → `integrations/mcp.py` loads the system's MCP-server
  tools via `langchain-mcp-adapters` (`mcp_tools_for(id)`); httpx kept as fallback. **Next:** P2
  (give looping agents real tools so create_agent loops) + bind connector MCP tools into the live
  agents + verify vs a real hosted server; P4 middleware activation; P5/P6. `deepagents` deferred.
- Verified: ruff clean, **mypy --strict clean (65 files)**, **69 pytest tests green**. Live
  Postgres/LLM/Jira/Plane runs pending real `.env` credentials.
- **Reviewer + Fixer + Jira-style UI (2026-06-15, decisions #22/#23):** Reviewer (deep one-pass
  `CodeReview`, severity tags, policy-gated auto-merge) and Fixer (bounded fix loop) are real now,
  not stubs. New `SpecRecord` persistence + `RunRecord.status`/`task_sink_id`; per-agent `trigger`
  config (`agents:` map). UI rebuilt on Tailwind+HTMX+Alpine (sidebar shell, SSE run detail,
  Approvals, searchable PM runs, Agents view). Roadmap/status in
  `docs/plan/agent_runtime_and_connectors_plan.md` §10–§13.
- **A1 / P2 — Dev agent loop (2026-06-16):** `CodingAgent` now runs a bounded `create_agent` tool
  loop (`DevToolkit`: read_file, list_files, search_code, run_command), detects test command /
  commit convention / PR template, outer test-fix loop up to `MAX_CODE_ITERATIONS=3`. New
  `src/ash/toolkits/dev.py`. Light/dark theme toggle added to all UI pages (CSS custom properties,
  FOUC-prevention script, Alpine.js toggle in topbar).
- **Roadmap complete (2026-06-16):** All remaining items from
  `docs/plan/agent_runtime_and_connectors_plan.md §13` are now shipped:
  - **A4 dispatch** — `AgentPolicy.trigger` default changed to `"auto"`; `_trigger_gate()` on
    `BaseAgent` calls `interrupt()` when `trigger="manual"`; `POST /ui/runs/{id}/trigger` resumes
    with `"run"`. Research/Coding/Reviewer/Fixer all gate on their trigger policy.
  - **P4b** — Reviewer interrupts for merge approval when `auto_merge_on_approve=True` AND
    `require_human_for_merge=True`; `/ui/runs/{id}/approve` resumes with `"approve"` → merge.
  - **C1 rest** — per-kind discriminated config schemas (`GitHubConnectorConfig`,
    `JiraConnectorConfig`, `PlaneConnectorConfig`, `MCPHTTPConnectorConfig`, `FileConnectorConfig`);
    `GET /ui/connectors/{id}/health` + HTMX health-dot fragment; Alpine multi-step add-connector
    wizard in `connectors.html`; `POST /ui/connectors` create endpoint; `mcp_tools_for_url`.
  - **RFC agent** — real `RFCAgent` + `RFCDocument` schema with `to_markdown()`; opt-in via
    `agents.rfc.trigger: auto`; `RFCState` in `WorkflowState`; `rfc` node in graph between
    `pm_publish` and `research`; `agent_rfc` override in `Settings`.
- **Current state:** 140 pytest tests, ruff + mypy --strict clean (71 source files). Live
  Postgres/LLM/Jira/Plane runs pending real `.env` credentials.
- **Open follow-ups:** Alembic migrations (stopgap `ADD COLUMN IF NOT EXISTS` backfills still
  in `db/base.py:_PG_COLUMN_BACKFILLS`); bind connector MCP tools into live agents (A1 note);
  A5 research sinks; deepen code grounding.
- **IMPLEMENTED (2026-06-17, decision #26) — per-story fan-out & oversight (F0–F8):** the **story is
  the unit of execution** inside one run — `WorkflowState.stories[ticket_id]` (reducer-merged) driven
  by a sequential `story_router`→`story_build` subgraph loop (`graph/builder.py` + `graph/stories.py`
  + node-adapter story scoping in `graph/nodes.py`). One **PR per story, built one by one**, in
  dependency order; **per-story retry** (resume at the failed story) + **manual regenerate**
  (`/ui/runs/{id}/stories/{ticket}/rerun`) via `Runner.retry_run(ticket_id, from_step)`; **no
  duplicate PRs** (deterministic per-ticket branch + persisted `branch`/`pr_url`, Coding updates the
  existing PR). PM **single (default)/multiple** `story_mode` + post-PM story selection at the review
  gate. **Context-min (F7):** Chroma indexes line-ranged chunks (not whole files) + `read_file(path,
  start, end)`. **Analytics (F8):** `AgentRunMetric` (tokens in/out + time + model per agent/story) →
  run-detail totals + per-story chips, Agents rollups, Dashboard KPIs. **RFC (F6):** one per run +
  Markdown preview + collapse fix. New DB: `StoryRecord`, `AgentRunMetric`, `AgentTask.ticket_id`,
  `RunRecord.story_mode` (PG backfills). Structured outputs stay LangChain-native (Instructor
  fallback only). **166 pytest green, ruff + mypy --strict clean (77 files).** Design + file map:
  **`docs/plan/per_story_fanout_and_oversight_plan.md`**.
