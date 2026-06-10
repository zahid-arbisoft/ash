# Agentic Software House (ASH)

A platform that behaves like a **software house with effectively unlimited staff** — except the
staff are **agents** (PM, Researcher, Dev, QA, Docs, Reviewer, Fixer) and the **clients** are humans
who provide requirements, choose integrations, define the loop flow, and keep oversight via feedback
gates. Built as a reusable, config-driven **loop engine** wrapped in a self-feeding loop, with a
Django **control plane** for multi-tenant persistence and (later) an API/UI.

> Design & rationale live in [`docs/plan/loop_engineered_sdlc_plan.md`](docs/plan/loop_engineered_sdlc_plan.md)
> (the authoritative plan). This README is the quickstart.

## How it works (today)

```
issue  ──►  PM spec  ──►  Board (specs/tickets: .md/.json today, Jira/Plane/Trello later)
                     └──►  worktree  ──►  Research  ──►  Coding  ──►  commit
                                                                       └──►  PR (CODE) ──► review ──► merge
```

- **Specs go to the Board; code goes to the PR** (kept strictly separate).
- **Human-in-the-loop is a toggle** (`ApprovalGate`) — one flag away from autonomous.
- **Triggers (inputs) and sinks (outputs) are pluggable connectors**, selected per project in config.
- Each ticket runs in its own **git worktree** for parallel safety.

## Monorepo layout

```
.
├── engine/src/ash/            # the ENGINE (framework-agnostic): agents, pipeline, connectors, tools
├── apps/house/                # Django app: Client → Project → Run, admin, `build` command
├── config/                    # Django project: settings/{base,dev,prod}.py, urls, wsgi, asgi
├── projects/<name>.yaml        # per-client/engagement config (repo, board, autonomy, budget)
├── skills/<name>/SKILL.md      # per-project persistent context for agents
├── docs/{plan/,…}             # design docs + the authoritative plan
├── tests/                      # pytest (engine)
├── runtime/                    # gitignored: sqlite db + per-project board/state/worktrees
├── manage.py · pyproject.toml · justfile · Dockerfile · docker-compose.yml
```

The engine never imports Django; the Django control plane imports the engine.

## Quickstart

```bash
just setup                 # venv + editable install (engine + Django + dev tools) + migrate
cp .env.example .env       # then fill in credentials (see below)
just doctor                # sanity-check LLM provider/model/key
```

### Configure `.env`
- **LLM** (provider-agnostic): `LLM_PROVIDER=openai` for an OpenAI-compatible gateway (LiteLLM/
  Ollama/vLLM) via `LLM_BASE_URL`, or `anthropic` for the native API. Set per-role models
  (`PM_MODEL`, `DEV_MODEL`, …).
- **Git**: the engine pushes over **HTTPS via `gh`** (`gh auth setup-git`), independent of the
  clone's origin. `LOCAL_REPO_PATH` points at an existing clone of the work-target repo.

## Common commands

```bash
just list plane                 # list open issues from the project's source repo
just spec plane 9213            # PM spec for one issue -> Board
just build plane 9213           # full build-team flow (CLI) -> draft PR (code)
just house-build plane 9213     # same, but via the control plane (persists a Run row)
just serve                      # control-plane admin UI at http://127.0.0.1:8000
just lint · just fmt · just test · just check     # quality
just docker-build · just docker-up                # containerized control plane
```

## Status

Phase 0 (PM spec) and Phase 1 (build-team flow: Research → Coding → PR) work end-to-end; runs persist
via the control plane. **Caveat:** code-generation grounding is still shallow — review generated PRs;
don't trust them blindly. Next: deepen grounding, then a Reviewer agent + orchestration (LangGraph),
then the scheduled heartbeat. Multi-tenant SaaS packaging is a later layer that must not change the
agent loop. See the plan for the full roadmap.
