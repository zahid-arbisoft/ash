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
3b. **Specs → local record + integration; code → PR** (§4c). PM always writes a local `.md`/`.json`
   spec to `runtime/board/`. Tickets are created in the selected integration (Plane/GitHub/Jira via
   `create_issue`) when `integration_id` is set. PRs carry **implementation only**.
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
  `db/crypto.EncryptedString`, key = `SECRET_KEY`). Issue sources + ticket creation = pluggable
  **integrations** (`src/ash/integrations`: GitHub/Jira/Plane behind `IssueProvider` with
  `fetch_issue`, `create_issue`, `post_comment`). FE = **Jinja2** at `/` (`src/ash/web`); admin =
  **SQLAdmin** at `/admin` (`src/ash/admin`, env creds). Per-run `intake_mode`
  (`raw_to_spec`/`spec_ready`/`raw_to_dev`/`spec_file`) routes use/skip PM. `spec_file` mode accepts
  `.md`/`.txt`/`.pdf`/`.docx` uploads; `utils/file_extract` converts to Markdown. Tools are 3-layer:
  `clients/` → `toolkits/` → agents. Config is hybrid: `pydantic-settings` + `projects/<name>.yaml`.
  Quality gates: ruff + **mypy --strict** + pytest, enforced in CI + pre-commit.

## Current status
- **Re-architected (2026-06-11)** to FastAPI + async + LangGraph + Postgres + LangChain (decisions
  #15–#18; #14 Django removed), then added **integrations + admin + UI** (decision #19): pluggable
  GitHub/Jira/Plane issue sources, per-run intake routing (Intake→[PM?]→Research→Coding→Reviewer→
  Fixer→Merge), SQLAdmin at `/admin`, Jinja2 UI at `/`. PM/Research/Coding real; Reviewer/Fixer stubs.
- **2026-06-12:** Added `spec_file` intake mode (upload `.md`/`.txt`/`.pdf`/`.docx` via admin,
  PM converts to Spec via LLM); `create_issue` on all integrations (PM creates tickets in selected
  integration after spec generation); structured logging across runner/nodes/agents; board_sink
  removed as a configurable parameter (local write is a fixed side-effect).
- Verified: ruff clean, **mypy --strict clean (60 files)**, **65 pytest tests green**.
- **Open follow-ups:** Alembic migrations (tables are `create_all` now), wire comment-back into a
  node, real Reviewer (maker/checker), bounded Fixer loop, move worktree cleanup Coding→Merge,
  deepen code grounding (don't trust generated code yet). Keep human gates until each layer earns it.
