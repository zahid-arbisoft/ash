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
  `LLM__PROVIDER=openai` + `LLM_BASE_URL=...` (NOT `anthropic`). Per-agent overrides via
  `AGENT_<NAME>__MODEL`. Agents force structure via `.with_structured_output`.
- `gh` CLI is authenticated as `zahid-arbisoft`. **Git auth = HTTPS via gh** (`gh auth setup-git`);
  the engine fetches/pushes over the HTTPS URL, NOT the clone's SSH origin (no ssh-agent headless).
- Local clone via `LOCAL_REPO_PATH` (origin=fork). Without it, Research/Coding **skip gracefully**
  so a PM-only run still completes.
- **Python ≥3.12** (target 3.12; 3.13 also fine). `.venv` is built with `python3.13` here since 3.12
  isn't installed locally and the old 3.14 venv can't take the langchain/langgraph wheels. Engine is
  editable-installed (`pip install -e .[dev]`). Commands live in the `justfile` (`just --list`).
- **Architecture:** single package `src/ash/` (no Django). Entry = **FastAPI** (`src/ash/api`,
  `POST /runs` / `GET /runs/{id}`); orchestration = **LangGraph** (`src/ash/graph`); run state of
  record = **Postgres checkpointer** (AsyncPostgresSaver). Tools are 3-layer: `clients/` →
  `toolkits/` → agents. Config is hybrid: `pydantic-settings` + `projects/<name>.yaml`. `config`
  finds repo root via `projects/`/`pyproject.toml` (or `ASH_ROOT`). Quality gates: ruff + **mypy
  --strict** + pytest, enforced in CI (`.github/workflows/ci.yml`) + pre-commit.

## Current status
- **Re-architected (2026-06-11)** to FastAPI + async + LangGraph + Postgres + LangChain (decisions
  #15–#18; #14 Django removed). Graph: PM→Research→Coding→Reviewer→Fixer→Merge over a namespaced
  `WorkflowState`, checkpointed in Postgres, served over FastAPI. PM/Research/Coding are real;
  Reviewer/Fixer are stubs.
- Verified: ruff clean, **mypy --strict clean (36 files)**, **30 pytest tests green**, FastAPI + CLI
  boot. Live Postgres/LLM runs pending real `.env` credentials.
- **Open follow-ups:** real Reviewer (maker/checker separation), bounded Fixer loop, move worktree
  cleanup from Coding→Merge once Reviewer/Fixer are real, deferred post-comment, deepen code grounding
  (don't trust generated code yet). Keep human gates until each layer earns removal.
