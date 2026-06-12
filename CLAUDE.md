# Standing instructions (read me at the start of every session)

This file is my own working memory for this project. If I forget how we work, I re-read this and
the plan before doing anything else.

## Source of truth
- The plan is **`docs/plan/ash_architecture_and_plan.md`**. It is authoritative. The original source
  specs live in **`docs/sources/`**.
- **Every decision and every implementation change is recorded in the plan**, and appended to its
  **Changelog (§11)**. The plan never lags the code. Do this in the same turn as the change — don't
  defer it.
- New decisions also get a row in the plan's "Locked Decisions" table (§7) when they're a choice,
  not just code.

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
- **Open follow-ups:** the create_agent/MCP/HITL phase above; Alembic migrations (tables are
  `create_all`); real Reviewer (maker/checker) + bounded Fixer; deepen code grounding.
