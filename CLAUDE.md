# Standing instructions (read me at the start of every session)

This file is my own working memory for this project. If I forget how we work, I re-read this and
the plan before doing anything else.

## Source of truth
- The plan is **`docs/plan/loop_engineered_sdlc_plan.md`**. It is authoritative.
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
   Jira/Plane/Trello later) for client oversight; PRs carry **implementation only**. The skeleton's
   spec-in-PR is temporary and must be corrected when the Coding agent lands.
4. **Repo topology is per-project**: `fork` / `single` / `closed-source(private)`.
5. **Sequencing discipline:** do NOT build triggers/sinks/UI until the core loop (Phases 0–3) is
   trustworthy. Keep human verification gates until the layer below has earned removal.

## Environment notes
- LLM is provider-agnostic. User runs a **LiteLLM gateway** (`/v1`) → use `LLM_PROVIDER=openai`
  (NOT `anthropic`, which would double the `/v1` path and use the wrong wire format).
- Models (Groq): PM=`gpt-oss-120b`, Dev/Fixer=`qwen3-32b`, Reviewer=`llama-3.3-70b-versatile`.
  LLM client auto-falls back to JSON mode when tool-calling validation fails (small models).
- `gh` CLI is authenticated as `zahid-arbisoft`. **Git auth = HTTPS via gh** (`gh auth setup-git`);
  the engine fetches/pushes over the HTTPS URL, NOT the clone's SSH origin (no ssh-agent headless).
- Local clone: `LOCAL_REPO_PATH=/Users/zahid.ali/Documents/python/oss/plane` (origin=fork).
- Python 3.14, venv at `.venv`. Engine is editable-installed (`pip install -e .[server,dev]`), so no
  `PYTHONPATH` needed. Commands live in the `justfile` (`just --list`). Tooling: ruff + pytest.
- **Monorepo:** engine = `engine/src/ash` (Django-free). Control plane = Django
  (`config/` project w/ split settings + `apps/house/` app: Client→Project→Run, admin, `manage.py
  build`). Engine never imports Django; Django imports the engine. DB = SQLite `runtime/ash.sqlite3`.
  Docs/plan under `docs/`. `config.py` finds repo root via `projects/`/`pyproject.toml` (or `ASH_ROOT`).
- Root folder rename to `ash` is **pending** (user does it + rebuilds `.venv`); don't assume it's done.

## Current status
- Phase 0 (PM → spec) and Phase 1 **build-team flow** are working end-to-end:
  issue → spec→**Board** (`runtime/<proj>/board/`) → worktree → **Research** → **Coding** → commit →
  push → **PR carries code** → merge gate → worktree cleanup. Verified live (PRs #2/#3) + persisted
  via Django (`manage.py build`, Run rows).
- **Open follow-up (important):** agent grounding is shallow — coding v1 fabricated internal paths
  for Plane (TS/MobX) like `apps/admin/*.jsx`+Redux. Deepen research/coding: verify paths vs the real
  tree, read real files before editing. Don't trust generated code yet.
- Spec-quality validation on a strong model still a useful human check.
- Next options: (a) deepen grounding, (b) Phase 2 Reviewer agent + LangGraph + checkpointing,
  (c) Phase 4 heartbeat. Keep human gates until each layer earns removal.
