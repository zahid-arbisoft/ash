# Loop-Engineered SDLC System — Analysis & Plan

> Source documents (under `docs/sources/`): `agent_architecture.md` (multi-agent SDLC design) and
> the AI-PM-Engineering multi-agent boilerplate spec + design, reconciled with the "loop
> engineering" pattern (a self-running agent harness).
> Goal: understand the requirements, reconcile them, and lay out a way forward.
>
> **Working agreement:** this plan is the source of truth. Every decision and every implementation
> change is reflected here (see the Changelog at the bottom) so the plan never lags the code.

---

## 0. Product Vision — Agentic Software House (ASH) (north star)

> **ASH** = **A**gentic **S**oftware **H**ouse — the project's name and the import package (`ash`).

We are building a **platform/tool (possibly SaaS) that behaves like a software house with effectively
unlimited resources.** Its "staff" are agents (PM, Researcher, Dev, QA, Docs, Reviewer, Fixer); its
"clients" are humans.

- **Client (human):** provides requirements/instructions, chooses integrations, **defines the loop
  flow**, and keeps oversight — giving feedback at each step (loop-engineering HITL).
- **Software house (platform):** assigns its agent "staff" to do the work and surfaces progress and
  decision points back to the client through the client's chosen channels.
- **Engagement:** one client's body of work. The platform is **multi-tenant** — many clients running
  in parallel, each isolated, each with their own config / connectors / flow.
- **Today:** single tenant (you), one project (Plane). The architecture must **not** bake in
  single-tenant assumptions.

### 0.1 Domain model (shared vocabulary)
| Concept | In the platform | Today (Plane) |
|---------|------------------|----------------|
| Software house | the platform/engine | this repo |
| Client / tenant | human + their config & integrations | you |
| Engagement / project | a unit of work for a client | the Plane project |
| Staff | agent roles (PM, Research, Dev, QA, Docs, Reviewer, Fixer) | same |
| Intake | how work enters (issues, UI, board, chat) | GitHub issues |
| **Board** | where **specs/tickets** live for client visibility (Jira/Plane/Trello/file) | `.md`/`.json` (for now) |
| **Delivery** | **implementation** as a PR → review → merge to base | fork PRs |
| Oversight | human feedback gates at each step | `ApprovalGate` |

> **Separation rule (important):** **specs go to the Board; code goes to the PR.** A spec is *not*
> the PR payload. See §4c. The Phase-1 skeleton temporarily put the spec in the PR only to prove git
> plumbing — that is being corrected.

This north star **reframes but does not invalidate** the phased plan. Phases 0–5 build the single
"software house" core; multi-tenancy / SaaS packaging is a later layer that changes *how we isolate
and serve many clients*, not the agent loop itself. Sequencing discipline (§10) still applies.

---

## 1. Executive Summary

Your `agent_architecture.md` describes a **linear pipeline**: PM → Dev → Reviewer → Fixer,
triggered once per requirement. The **loop-engineering** pattern describes a **self-running harness**
that discovers its own work on a schedule, runs agents in parallel-safe isolation, separates the
maker from the checker, and remembers state across runs.

These are not in conflict — your pipeline is the *body* of one loop iteration. The thing you're
missing (and what loop engineering is really about) is everything *around* the pipeline that lets it
run unattended and repeatedly: **the heartbeat, the memory, the verification separation, and the
connectors.**

**The core mental shift:** stop being the person who triggers the pipeline. Build the system that
triggers it on your behalf, feeds itself, and bridges its own memory between runs.

**Primary design constraint — reusability across the org:** this is not a one-off tool for Plane.
It is a **generic loop engine** that gets pointed at *many* projects. Plane is just the first
target. Every design choice below favors **one engine, many project configs** over per-project
copies (which would drift and rot — the "intent debt" the article warns about). See §9.

---

## 2. What "Loop Engineering" Actually Means (distilled)

A loop is a recursive, goal-oriented system that runs cycles **until a verifiable condition is
true**, without you in the inner loop. Its building blocks:

| Component | Role | One-line definition |
|-----------|------|---------------------|
| **Automations** | The heartbeat | Scheduled tasks that *discover work themselves* (CI failures, open issues) and triage it into an inbox. |
| **Worktrees** | Parallel safety | Git worktrees isolate concurrent agents so they don't collide on files/branches. |
| **Skills** | Persistent context | `SKILL.md` bundles encoding conventions, build steps, domain knowledge — so agents don't re-derive context every run ("intent debt"). |
| **Connectors (MCP)** | Environmental integration | Links to issue trackers, DBs, Slack, GitHub — turns the loop from suggestion-maker into active participant. |
| **Sub-agents** | Separated verification | Maker drafts, a *different* checker verifies. A model must not grade its own work. |
| **External memory** | Continuity | Markdown/Linear/logs. "The model forgets, the repo doesn't." Bridges disconnected runs. |

**Key principles to internalize:**
1. **Verification stays your responsibility.** Loops err unattended; review what ships.
2. **Comprehension debt is the real risk.** The faster it ships, the wider the gap between what
   exists and what you understand. Read the output.
3. **Loops amplify judgment in *either* direction.** They don't replace understanding; they scale it.
4. **Token cost compounds.** Verifier passes + connector calls + retries across many runs add up.
   Budget per-loop spend explicitly.

---

## 3. Analysis of `agent_architecture.md`

### Strengths
- Clean role decomposition (PM/Dev/Reviewer/Fixer) with explicit triggers and output contracts.
- Correct central insight: **PM/spec quality gates everything downstream.**
- Sensible phased MVP (PM + Dev + human review first).

### Gaps (especially through a Loop-Engineering lens)
| Gap | Why it matters | Loop-Eng remedy |
|-----|----------------|-----------------|
| **No heartbeat** | The pipeline only runs when a human hands it a requirement. That's prompt engineering, not loop engineering. | Add a scheduled **Automation/Triage** stage that finds work (issues, CI failures) and enqueues it. |
| **Fixer↔Reviewer loop is unbounded** | Real failure mode: oscillating fixes, infinite loops, "fix X, now Y breaks." | Hard **loop bounds** (`MAX_FIX_ITERATIONS`), convergence detection, escalate-to-human exit. |
| **Reviewer can be the same model as Dev** | Maker grading its own work → false "done" signals. | Enforce **maker/checker separation** (distinct agent, ideally distinct model/prompt). |
| **State has no failure/resume model** | A crash restarts the whole run; no partial progress. | **Checkpointing** (LangGraph + Postgres) + richer state: `status`, `error`, `attempt_count`, `history`. |
| **No external memory across runs** | Each requirement starts cold; no learning, no continuity. | An **external state file / board** the loop reads and writes every cycle. |
| **No sandboxing/parallel safety** | An agent with repo write access running concurrently = collisions and risk. | **Git worktrees** per ticket + scoped GitHub permissions. |
| **Dev Agent does too much** | Explore + plan + implement + test in one node is the least reliable, hardest-to-retry step. | Split it into a **build team** of focused agents (Research/Spike, Dev/Coding, Documentation) — read-only research separated from write-heavy coding. See §4a. |
| **pgvector "code memory" is premature** | Semantic search is a scale luxury, not a core-loop need. | Defer. ripgrep + reading files covers ~90%. |

---

## 4. Target Architecture: Pipeline-as-Loop-Body

```
        ┌──────────────────────── LOOP HARNESS (runs on a timer) ────────────────────────┐
        │                                                                                  │
        │   [Automation/Triage]  ──reads──>  External Memory (state.md / board)            │
        │      discovers work        <──writes── every stage                               │
        │          │                                                                       │
        │          ▼                                                                       │
        │     Ticket Queue                                                                 │
        │          │                                                                       │
        │   ┌──────┴───── per ticket, in its own git worktree ─────────┐                   │
        │   │  PM Agent (spec)                                          │                  │
        │   │      ↓                                                    │                  │
        │   │  [RFC Agent] (optional, config-gated) ── human/auto OK ─┐ │                  │
        │   │      ↓                                                  │ │                  │
        │   │  BUILD TEAM:  Research/Spike → Dev/Coding → Docs        │ │                  │
        │   │      ↓                                                    │                  │
        │   │  PR created (via Connector)                               │                  │
        │   │      ↓                                                    │                  │
        │   │  Reviewer (SEPARATE checker agent) ── approved ──> Merge  │                  │
        │   │      ↓ changes_requested                                  │                  │
        │   │  Fixer ──(bounded N cycles)──> back to Reviewer           │                  │
        │   │      ↓ exceeded bound / stuck                             │                  │
        │   │  Escalate to human inbox                                  │                  │
        │   └───────────────────────────────────────────────────────────┘                 │
        │                                                                                  │
        └──────────────────────────────────────────────────────────────────────────────────┘
```

- **Connectors (MCP):** GitHub (PRs/issues), optionally Slack/Linear for triage + escalation.
- **Skills (`SKILL.md`):** project conventions, build/test commands, review checklist — loaded by
  every agent so they stop re-deriving context.
- **Sub-agents:** Reviewer is a genuinely separate agent from the build team.

### 4a. Agent roster (the "Dev agent" is a team, not one agent)

The single Dev agent is split into focused, independently-retryable roles. All are optional/
composable per project via config (`pipeline:` ordering), so a project can run a lean or rich team.

| Agent | Mode | Responsibility |
|-------|------|----------------|
| **PM** | think | Issue → structured spec (Phase 0, done). |
| **RFC** *(optional)* | think | Spec → short RFC/design doc for human (or auto) sign-off **before** coding. Config-gated; see §4b. |
| **Research / Spike** | read-only | Explore the codebase (ripgrep/AST/file reads), answer open questions, produce an implementation plan + affected files. No writes — cheap to re-run. |
| **Dev / Coding** | write | Turn the plan into actual code changes + tests in a worktree; commit. The only write-heavy coder. |
| **Documentation** | write | Update project docs/changelog/READMEs related to the change. |
| **Reviewer** | check | Separate checker — quality/security/spec-compliance. Never the same agent that wrote the code. |
| **Fixer** | write | Apply minimal patches from review feedback; bounded loop. |

Rationale: separating **read-only research** from **write coding** makes the risky step small and
retryable, and lets a weak/cheap model do research while a stronger model codes (or vice-versa) —
each role has its own model in config.

### 4b. Optional RFC stage (between PM spec and build)

Some teams want a design review before code. Modeled as an **optional pipeline node**, off by
default:

```yaml
# projects/<name>.yaml
pipeline:
  rfc:
    enabled: false            # turn on per project
    require_human_approval: true   # uses the same ApprovalGate (§7b)
```

- When enabled: RFC Agent drafts a short design doc from the spec; it passes through `ApprovalGate`
  (human or auto) before the build team starts.
- When disabled: spec flows straight to Research/Spike. No code path differences elsewhere.
- Implemented later (tracked in §5 Phase 1.5 / backlog), but designed for now so the graph supports
  optional nodes from the start.

### 4c. Separation: specs go to the Board, code goes to the PR

A spec is a *planning artifact for the client*; a PR is *delivered implementation*. They have
different destinations and different lifecycles.

```
PM Agent ──► SPEC ──► Board sink            (Jira / Plane / Trello / .md|.json file)
                       └─ client sees/edits/approves tickets here (oversight)

Build team ─► CODE ─► Delivery sink (PR)    (branch → fork PR → review → merge to base)
                       └─ Reviewer/Fixer/human act here
```

- **Board sink** (specs/tickets): selected per client in config (`board:`). Today a local
  `.md`/`.json`; later Jira/Plane/Trello via connectors (§8). The PR must **not** be the spec's home.
- **Delivery sink** (code): the PR carries the *implementation* produced by the build team. Opened
  only once there is code; reviewed; merged to the base branch on approval.
- **Phase-1 correction (open task):** the walking skeleton wrote the spec into the PR to test
  plumbing. Next change: route the spec to the Board sink, and create the PR only for code from the
  Coding agent. Tracked in §5 Phase 1 + Changelog.

---

## 5. Way Forward — Phased Plan

Each phase is independently useful and ends in something you can run. **Every phase also ships its
documentation slice** (per §9.4) — the engine stays config-driven and the onboarding runbook grows
with it, so a teammate could point the loop at a new repo at any phase boundary.

### Phase 0 — Prove the PM Agent on a real Plane issue (highest leverage) — ✅ BUILT
> Status: implemented & smoke-tested (see Changelog 2026-06-10). Spec-quality validation against a
> strong model is the remaining human exit-criterion.
- Fetch a single real issue from `makeplane/plane` and feed its title/body to the PM Agent.
- One prompt: issue in → structured spec out (epic, technical_spec, tickets, risks).
- Validate spec quality by hand on a few real issues. **If specs are weak here, stop and fix this
  first** — per your own key insight, nothing downstream matters otherwise.
- No queue, no DB, no orchestration yet. Just `read issue → spec`.
- **Exit criteria:** specs you'd be comfortable handing to a junior engineer.

### Phase 1 — PM + build team, one issue, human review (against the fork)
- Clone the fork; add upstream remote; sync base branch (`preview`). Repo mode per §7a.
- Pick one issue → **Research/Spike** agent (read-only) produces a plan + affected files →
  **Dev/Coding** agent implements in a **git worktree** → **Documentation** agent updates docs →
  opens a **fork-internal PR**.
- Human reviews/merges manually (via the `ApprovalGate`, default `require_human=true`).
- Define `WorkflowState` with failure/retry fields from day one.
- Build incrementally (see scoping decision): a **walking skeleton** of the git→PR plumbing first,
  then layer in agent intelligence.
- **Correct the flow (next):** spec → **Board sink** (file today); the **PR carries code only**
  (§4c). The skeleton currently puts the spec in the PR — replace that once the Coding agent exists.
- **Exit criteria:** one real Plane issue → spec on the board → **code** PR in the fork, reviewed/
  mergeable.

### Phase 1.5 — Optional RFC stage *(backlog; build when prioritized)*
- Insert the **RFC Agent** between PM spec and the build team, gated by
  `pipeline.rfc.enabled` (§4b) and `ApprovalGate`.
- **Exit criteria:** with RFC enabled, a spec produces an RFC that must be approved before any code.

### Phase 2 — LangGraph orchestration + checkpointing + Reviewer — 🟡 PARTIAL
> Orchestration + checkpointing landed 2026-06-11 (decisions #15–#17): the graph is a LangGraph
> `StateGraph` (PM→Research→Coding→Reviewer→Fixer→Merge) over a namespaced `WorkflowState`, persisted
> by the AsyncPostgresSaver and exposed via FastAPI. Reviewer/Fixer are wired as **stubs**. Remaining
> for this phase: make the **Reviewer a real separate checker** (distinct model/prompt from Coding).
- Graph + **Postgres checkpointing** (resume on crash) — DONE (linear edges; conditional edges later).
- Add the **Reviewer as a separate checker agent** (distinct model/prompt from Coding) — TODO.
- Reviewer is an automated gate; **merge still passes through `ApprovalGate`.**
- **Exit criteria:** issue → PR → automated review comments, resumable across restarts.

### Phase 3 — Close the Fixer loop (bounded)
- Fixer reads review comments → minimal patch → re-runs tests → pushes → back to Reviewer.
- **Hard bound** (`MAX_FIX_ITERATIONS`, default 3) + convergence check. On exceed → `ApprovalGate`
  escalation → append to **`escalations.md`** (human inbox).
- **Exit criteria:** end-to-end loop on a single issue with safe termination + escalation record.

### Phase 4 — Add the heartbeat (this is where it becomes Loop Engineering)
- **Automation/Triage stage** on a schedule (cron / `/loop` / scheduled remote agent) that scans
  `makeplane/plane` **open issues**, filters/triages them, and enqueues tickets.
- Periodic **upstream-sync** step keeps the fork's main current before each ticket.
- **External memory file** (`state.md`) updated every cycle — done / failed / in-flight / escalated;
  next run continues from it.
- **Token budget guard:** track spend per ticket/day; pause + log when soft caps (~$2/ticket,
  ~$20/day) are hit.
- Unhandled cases → `escalations.md` triage inbox.
- **Exit criteria:** you walk away; tomorrow's run continues yesterday's work within budget.

### Phase 5 — Harden & scale (defer until 0–4 are solid)
- Skills library (`SKILL.md` per repo), parallel tickets across worktrees, Slack connector for
  escalation, token-budget guardrails per loop, LangSmith observability, pgvector code memory.

### Future capability — Claude Code–style agentic repo harness *(backlog)*
The Research and Coding agents currently explore the repo via `list_directory` / `grep_code` /
`read_file` / `search_codebase` tools, which mirrors how Claude Code works inside an editor.  
A further evolution is a **self-running "harness" mode** modelled directly on Claude Code:

- **Agent-controlled shell loop:** the agent gets a `bash` tool with a sandboxed subprocess;
  it can run `grep`, `find`, `cat`, `python -m pytest`, etc. itself — just like a human dev
  would in a terminal. No pre-defined toolkit; the agent composes the commands it needs.
- **Persistent working context:** across turns the agent accumulates a structured scratchpad
  (files read, grep results, test outcomes) so it doesn't re-explore on every iteration.
- **Interrupt-on-blocker:** if the agent reaches a decision point (e.g. ambiguous spec, missing
  credentials, a failing test it can't diagnose), it calls `interrupt()` to surface the blocker
  to the human rather than guessing.
- **Why defer:** the `bash` tool's sandboxing complexity (Docker sub-process, PTY, timeout,
  resource limits) is non-trivial. The current `run_command`/`DevToolkit` path already covers
  the 80 % case. Build this when shallow grounding is the measurable bottleneck.
- **Prerequisite:** Alembic migrations (replace `_PG_COLUMN_BACKFILLS` stopgap) and pgvector
  code memory should land first so the harness has a persistent index to work from.

---

## 6. Recommended Tech Stack (vs. the doc)

| Concern | Doc | Decision (implemented 2026-06-11) |
|---------|-----|----------------|
| LLM | GPT-5 / Claude Opus | **LangChain** `ChatAnthropic`/`ChatOpenAI`, provider-agnostic; `base_url` for LiteLLM/Ollama/vLLM. Per-agent model overrides. |
| Orchestration | LangGraph | **LangGraph** `StateGraph` ✓ with built-in checkpointing. |
| Web/entry | (absent) | **FastAPI** async app — `POST /runs` (background), `GET /runs/{id}` (checkpointer read). |
| Queue | Redis | **Defer.** `Runner` is the swap-in point; LangGraph handles orchestration today. |
| State of record | PostgreSQL | **AsyncPostgresSaver** (LangGraph checkpoints) keyed on `run_id`. `state.md`/board still serve as human-readable memory. |
| Code memory | pgvector | **Defer to Phase 5.** ripgrep + file reads (`clients/code_intel.py`) first. |
| GitHub | API + GitPython | **async httpx** `GitHubClient` (issues/comments) + **GitPython** worktrees + `gh` for PRs. |
| Observability | LangSmith | Defer (Phase 5). |
| Parallel safety | (absent) | **git worktrees** per ticket ✓. |
| Quality | (absent) | **ruff + mypy --strict + pytest-asyncio**, enforced in **GitHub Actions** CI; Python ≥3.12. |

---

## 7. Locked Decisions (resolved 2026-06-10)

| # | Decision | Detail |
|---|----------|--------|
| 1 | **Heartbeat = GitHub Issues** | Discover work from `makeplane/plane` issues (upstream, **read-only**). No CI/board sources yet. |
| 2 | **Human-in-the-loop, toggleable** | Required *for now*, but gated behind a config flag so it can be turned off with one switch. Plug-and-play, not hardcoded `if`s. |
| 3 | **Escalation = local `.md` inbox** | We don't own upstream issues, so stuck/escalated tickets append to a local `escalations.md`. Gated by the same human toggle as #2. |
| 4 | **Token budget = soft caps** | Start with **~$2/ticket** and **~$20/day** (configurable). Loop pauses + logs on breach. Tune after observing real costs. |
| 5 | **Repo topology = configurable per project** | Plane uses the *fork* model (read upstream, write fork). The engine must **also** support *single-repo* and *closed-source/private* projects (origin only, no upstream). See §7a. |
| 6 | **Triggers & sinks are pluggable (build later)** | Today: trigger = GitHub issues, sink = local JSON spec. The engine is designed so triggers (CI, boards, webhooks, a UI, Slack, manual) and sinks (Jira/Plane/Trello, your own Plane board) are **config-selected connectors**, not code changes. See §8. |
| 7 | **A UI is a later, separate layer** | Where a user picks trigger sources, submits work, and views specs/results. Sits *outside* the engine and reads its state/specs. Not part of the core loop (Phase 5+). See §8. |
| 8 | **"Dev" is a build team, not one agent** | Split into Research/Spike (read-only), Dev/Coding (write), Documentation (write), each with its own model. Composable per project via `pipeline:` config. See §4a. |
| 9 | **Optional RFC stage** | Some teams want a design-review RFC after the PM spec, before coding. Modeled as an optional, config-gated pipeline node (off by default). Build later (Phase 1.5). See §4b. |
| 10 | **Git auth = HTTPS via `gh`** | Engine fetches/pushes over HTTPS using `gh auth setup-git` credentials, independent of the clone's `origin` (which may be SSH). Avoids ssh-agent dependence in headless runs. |
| 11 | **Models (Groq via LiteLLM)** | PM=`gpt-oss-120b` (reasoning+tool calling); Dev/Fixer=`qwen3-32b` (code); Reviewer=`llama-3.3-70b-versatile`. LLM client has a **JSON-mode fallback** when a provider's tool-calling validator rejects output (e.g. small llama on Groq). |
| 12 | **Specs → Board, code → PR** | Specs/tickets publish to a **Board sink** (file today; Jira/Plane/Trello later) for client oversight. PRs carry **implementation only**. The skeleton's spec-in-PR is a temporary plumbing artifact to be corrected. See §0/§4c. |
| 13 | **Product = multi-tenant agentic software house** | North star (§0): agents are "staff", humans are "clients" who set requirements/integrations/flow and keep oversight. Multi-tenant, parallel engagements. SaaS packaging is Phase 5+; it must not alter the agent loop. |
| 14 | **~~Control plane = Django~~ (SUPERSEDED by #15)** | *Original:* a Django control plane (`apps/house`) persisted Client→Project→Run with an admin UI + `manage.py build`. **Removed 2026-06-11** when we adopted the boilerplate-spec stack: FastAPI replaces Django and the LangGraph Postgres checkpointer is the run state of record. Multi-tenant Client/Project tables can return later as FastAPI/SQLAlchemy app tables if needed (not the checkpointer's job). |
| 15 | **Web framework = FastAPI; orchestration = LangGraph; run state = Postgres checkpointer** | The entrypoint is an async **FastAPI** app (`POST /runs` → `run_id`, background task; `GET /runs/{id}` reads checkpointer state). The pipeline is a **LangGraph** `StateGraph` (PM→Research→Coding→Reviewer→Fixer→Merge) over one **namespaced** `WorkflowState`, persisted by the **AsyncPostgresSaver** keyed on `thread_id`=`run_id`. Replaces the hand-rolled sync `pipeline.py`. |
| 16 | **Engine is async; LLM via LangChain** | All agent/graph/client code is `async`; blocking git/subprocess calls run in `asyncio.to_thread`. The provider-agnostic LLM is a **LangChain** chat model (`ChatAnthropic`/`ChatOpenAI`, `base_url` for LiteLLM/Ollama/vLLM); agents force structure via `.with_structured_output(schema)`. Replaces the hand-rolled `LLMClient`. |
| 17 | **Layout = `src/` single package; config = hybrid** | Best-practice `src/ash/` single package (`api/`, `agents/`, `graph/`, `clients/`, `toolkits/`, `llm/`, `config/`). Tools are 3-layered: `clients/` → `toolkits/` (`BaseTool`) → agents. Config is **hybrid**: `pydantic-settings` `Settings` for engine secrets + per-agent model overrides, **plus** `projects/<name>.yaml` for the multi-tenant per-engagement layer (design rule #1 preserved). |
| 18 | **Agent roster kept; Reviewer/Fixer stubbed; quality gates hardened** | We keep ASH's PM/Research/Coding (real) and add Reviewer/Fixer as `BaseAgent` stubs (Phases 2–3). With no local clone, Research/Coding skip gracefully so a PM-only run completes. **mypy --strict** is enforced in CI (ruff → mypy → pytest); Python ≥3.12. Posting the spec back as an issue comment is deferred (the `post_comment` seam exists). |
| 19 | **Pluggable issue-source integrations + per-run intake routing + admin/UI** | Issue sources (GitHub / Jira / Plane) are DB-backed `Integration` rows behind one `IssueProvider` interface; secrets **encrypted at rest** (Fernet). A per-run **intake_mode** (`raw_to_spec` / `spec_ready` / `raw_to_dev`) drives a LangGraph **conditional edge** that uses or skips PM. App DB = **SQLAlchemy 2.0 async** (same Postgres); admin = **SQLAdmin** at `/admin` (env-credentialed); FE = **Jinja2** UI at `/`. New sources = new provider, no graph/agent changes. |
| 20 | **Intake mode semantics: `raw_to_dev` is the only mode that skips PM** | `raw_to_spec` = PM generates full spec + tickets from raw requirements. `spec_ready` = PM extracts tickets (stories) from a pre-written spec using a distinct prompt — PM is NOT skipped, the JSON-parsing shortcut is removed. `raw_to_dev` = PM skipped entirely; raw issue goes straight to the build team. PM is two graph nodes: `pm` (generate + checkpoint) → `pm_publish` (HITL interrupt → approve/reject → push tickets). |
| 21 | **Spec quality = prompt rules + deterministic validation (two layers)** | The org spec-quality standard (from arbisoft/ai-skillforge: `ai-first-engineering` + `blueprint`) is enforced, not hoped for. **Layer 1:** six hard rules in the PM system prompt (`_QUALITY_RULES`: no invented context, honor every explicit signal, calibrate scope to the ask, flag unknowns not guess, acyclic dependencies, complete risk assessment) + schema guidance (`Ticket.description` cold-start bar, `Spec.open_questions`). **Layer 2:** `agents/spec_validator.py` proves what's decidable in code (acyclic dependency graph, no dangling/self deps, unique ids, spike↔needs_research). On failure the PM does **one self-correction round** (errors fed back → regenerate); residual issues surface in `open_questions` for the human gate — a structurally broken spec never ships silently. Three skills vendored verbatim into `.claude/skills/`; standard documented in `docs/best_practices.md`. |
| 22 | **UI overhaul = server-rendered HTMX + Tailwind + Alpine (Jira-style)** | The rudimentary inline-CSS Jinja2 UI is rebuilt into a Jira-style oversight surface **without** a SPA/Node build target: FastAPI+Jinja2 stays the backbone; **Tailwind** (Play CDN → standalone CLI later) supplies the design system, **HTMX** drives partial updates + **SSE** live run status, **Alpine** handles local interactivity. Sidebar+topbar IA; shared macro/partial design system replaces per-template `<style>`; dedicated **PM-runs (searchable/paginated)**, **Agents**, **Connectors**, and **Approvals** sections. Chosen over React/Vue to keep one Python codebase. Detail + roadmap in `agent_runtime_and_connectors_plan.md` §12–§13. |
| 23 | **Every agent has a trigger mode (`auto`/`manual`) + connectors go MCP-first** | New per-agent `trigger` config (`projects/<name>.yaml` `agents:` map, env override `AGENT_<NAME>__TRIGGER`): **auto** = act on detected/assigned work (scheduler/webhook dispatch); **manual** = wait for explicit UI/API trigger. Orthogonal to `Autonomy` (which gates dangerous mid-loop steps). Connectors are reframed **MCP-first** (unlimited platforms via their MCP servers) with the legacy httpx providers kept as a **visible backup** in admin; per-kind config becomes discriminated Pydantic (no free-form JSON), with role/MCP validators and a connection health check; `task_sink_id` is persisted on `RunRecord`. See plan §10.0, §11. |
| 24 | **Failed runs retry from the failed step, not from scratch** | A run that fails at step X (RFC/Research/Coding/…) can be re-run from X without redoing the successful upstream work. `Runner.retry_run` forks the run's LangGraph checkpoint via `aupdate_state(as_node=<predecessor>)` so X becomes the next node, then `ainvoke(None)` completes the pipeline; each re-run node overwrites its namespace, clearing stale errors. Chosen over (a) marking the whole run dead + starting fresh (loses the spec/plan) and (b) auto-retry loops (a wrong-input failure would just re-fail) — a human inspects the error and clicks **Retry from <step>**. Structured-generation agents already run two-phase (explore→extract) and the explore phase is a hand-rolled `auto`-tool-choice loop, since `create_agent`'s `tool_choice="none"` synthesis turn 400s on Groq `gpt-oss-*`. |
| 25 | **The build unit is a ticket; Research is optional** | A run can set `ticket_id` to scope the build team (Research → Dev → Reviewer → Fixer) to a single spec ticket on its own branch (`brief()` returns a focused per-ticket brief); blank = build the whole spec as one PR (prior behaviour). Set via the run form or a per-ticket **Build this ticket** button on the spec view. Independently, **Dev no longer depends on Research**: worktree setup is shared (`agents/worktree.py`), and when Research is disabled/skipped Coding creates the worktree itself and builds from the brief with no plan — so a user can turn off the flaky Research agent and still ship. Full per-spec fan-out (auto-spawn one sub-run per ticket) is a later layer; today ticket selection is explicit (form field / button). |
| 27 | **PM tickets get a per-ticket elaboration pass (depth over a single compressed call)** | A comprehensive uploaded spec was yielding thin, few-line tickets because PM generated the **whole** spec (epic + tech spec + all tickets + risks) in **one** structured call — so each ticket got compressed to fit the output budget (worse on small models like `gpt-4o-mini`). Fix: after the skeleton spec + validation, PM runs a **focused second pass per ticket** (`PMAgent._elaborate_tickets`, gated by `pm_detail_tickets`, source-spec context capped by `pm_detail_context_chars`) so each ticket gets its own generation budget and comes out richly detailed. `Ticket` gains structured detail fields (`implementation_notes`, `affected_files`, `api_changes`, `data_model_changes`, `out_of_scope`) that force depth and feed the build team; the pass is **best-effort + structure-preserving** (keeps the validated id/type/dependencies, falls back to the original ticket on error). Detail propagates to `brief()`/`_ticket_brief`, the file Board, and the PM-run UI. |
| 26 | **Story = unit of execution; per-story fan-out, retry & regenerate inside one run (LangGraph-first)** | Supersedes #25's "one run = one ticket". The build phase becomes a **per-story subgraph keyed by `ticket_id`** inside a single run: `WorkflowState.stories: dict[ticket_id, StoryState]` (reducer-merged) + `story_order` + a sequential `story_router`→`story_build` loop. Stories run **one by one** in dependency order; each gets its **own PR** (deterministic branch `ash/<run>/<ticket_id>`, persisted `branch`/`pr_url` → Coding/Fixer **update, never duplicate**). PM gains a **single (default) / multiple** stories toggle. **Retry is per-story** (`retry_run(ticket_id, from_step)` re-enters the router → resumes at the failed story, skips completed ones); **manual per-story regenerate** (re-research / regenerate-PR / re-review / re-fix) uses the same fork. RFC is **always one per run**. UI: per-story timeline cards + per-PR progress + top-right PR link/dropdown; RFC Markdown preview + `hx-preserve` collapse fix. Full design: **`docs/plan/per_story_fanout_and_oversight_plan.md`**. |
| 28 | **Deployment topology = B1 (cloud control plane + local runner daemon)** | Code must never leave the on-prem boundary (data-residency; large repo cost/latency). Cloud runs the control plane (FastAPI + LangGraph + Postgres + UI/Approvals + PM/RFC agents). On-prem runs the **runner daemon** (filesystem access, worktrees, Chroma indexing, git push). Runner dials out to control plane (no inbound AWS ports); only extracted code snippets reach the LLM vendor. B2 (GitHub Actions) rejected: designed for CI, not interactive HITL agent loops. **Gated on Alembic migrations** (remove `_PG_COLUMN_BACKFILLS` stopgap) before runner seam work starts. Full analysis + retrieval quality roadmap + Deep Agents placement: **`docs/plan/deployment_topology_and_code_extraction_plan.md`**. |

### 7a. Repo topology — configurable per project

The engine supports three modes via project config (`work.mode`), so it fits both the Plane fork
experiment and the org's own (often private) repos.

```
mode: fork        UPSTREAM (read issues)  ──►  ORIGIN/FORK (write: branches, PRs, merges)
                  e.g. makeplane/plane          e.g. zahid-arbisoft/plane
                  periodic `git fetch upstream` keeps fork base branch in sync

mode: single      ORIGIN (read issues AND write)        ← one repo, no upstream
                  e.g. your-org/service                   typical for org-owned repos

closed-source     same as `single`, but private          ← visibility/auth property,
                  token needs `repo` (private) scope        not a separate mode
```

- **fork** (Plane, now): read issues from upstream (read-only), write to fork; no upstream PRs while
  building/testing; periodic upstream-sync prevents drift.
- **single**: issue source repo == work target repo; no `upstream_remote`. Most org projects.
- **closed-source/private**: `single` (or `fork`) with a token scoped to private repos; no
  unauthenticated public reads. The engine treats this as an auth detail, not new logic.
- Token scope is therefore **per project**: minimum needed for that project's mode.
- Each ticket still = one issue → one branch in its own **git worktree** → PR into the base branch.

### 7b. The approval-gate abstraction (decisions #2 + #3)

A single small primitive used at every human-gated point, so autonomy is one flag away:

```python
# pseudocode — the only place "human input" logic lives
class ApprovalGate:
    def __init__(self, require_human: bool): ...
    def check(self, kind: str, payload: dict) -> Decision:
        # kind in {"merge", "escalation"}
        if not self.require_human:
            return Decision.AUTO_APPROVE        # fully autonomous path
        return Decision.WAIT_FOR_HUMAN          # park in escalations.md / pause
```

- `REQUIRE_HUMAN_APPROVAL=true|false` (env/config). Optionally per-gate
  (`REQUIRE_HUMAN_FOR_MERGE`, `REQUIRE_HUMAN_FOR_ESCALATION`) for finer control.
- Gates that consult it: **merge** (Reviewer→Merge) and **escalation** (Fixer bound exceeded).
- Flip to `false` → loop merges and self-resolves unattended; everything else unchanged.

---

## 8. Extensibility — Pluggable Triggers, Sinks & Integrations (future)

A validated assumption to design for now and build later: **the loop should not be hardwired to
"GitHub issue in, JSON spec out."** Both ends are connectors selected per project in config.

```
   TRIGGERS (inputs)                 ENGINE                    SINKS (outputs)
   ─────────────────                ────────                  ───────────────
   GitHub issues   (now) ─┐                          ┌─► JSON spec file        (now)
   CI failures            ├─► triage ─► PM ─► Dev ─►──┤─► Jira / Plane / Trello (later)
   Project boards         │   ...      Reviewer Fixer │   your own Plane board
   Webhooks / Slack       │                           └─► GitHub PR on the fork (Phase 1)
   UI submission   (later)┘
   Manual / cron
```

- **Triggers (work discovery):** GitHub issues today. The same `Trigger` interface admits CI
  failures, boards, webhooks, Slack, cron, **or a UI where a user submits/initiates work** and
  picks the source. Selected via `projects/<name>.yaml`.
- **Sinks come in two kinds (§4c):**
  - **Board sink** (specs/tickets) — a `publish_spec(spec)` interface. Local `.md`/`.json` today;
    **Jira / Plane / Trello** (or a client's own Plane board) later. This is where the client sees
    and approves planned work.
  - **Delivery sink** (code) — the **PR** produced by the build team → review → merge to base.
  - Multiple sinks can be active at once; both are selected per client/project in config.
- **Integrations:** these are MCP-style connectors. Auth/config lives in project config; the engine
  core stays connector-agnostic.
- **UI:** a later, separate layer (Phase 5+) outside the engine. It reads `state.md`/specs and lets
  a human choose triggers, submit work, and review results. If specs land in Jira/Plane/Trello,
  those boards already serve as a UI — so a custom UI is optional, not blocking.

**Sequencing:** do NOT build triggers/sinks/UI until the core loop (Phases 0–3) is trustworthy.
This section captures the design intent so the interfaces are shaped right; the implementations
come after Phase 3 (sinks/triggers) and Phase 5+ (UI).

---

## 9. Replicability & Multi-Tenancy — One Engine, Many Clients

The whole point: build the loop **once**, then onboard new clients/projects with config, not code.
This is also the foundation of the multi-tenant "software house" (§0): a *project* today generalizes
to a *client engagement* tomorrow.

> **Multi-tenancy (later layer, design-for-now):** each client is an isolated tenant with its own
> config, connectors, board, credentials, runtime state, and budget. Engagements run in parallel
> without interfering (separate worktrees, separate runtime dirs, separate secrets). SaaS packaging
> (auth, billing, per-tenant secret storage, a control plane) is a Phase-5+ concern and does **not**
> change the agent loop — only how tenants are isolated and served. Do not build it until the
> single-tenant core (Phases 0–3) is trustworthy.

### 9.1 Separation of concerns
| Layer | What it is | Changes per project? | Lives in |
|-------|-----------|----------------------|----------|
| **Engine** | Generic agents, graph, loop harness, gates, budget guard | No | `src/ash/` |
| **Project config** | Repo coords, branch, budget, autonomy flags, schedule | Yes | `projects/<name>.yaml` |
| **Project skills** | Conventions, build/test commands, review checklist | Yes | `skills/<name>/SKILL.md` |
| **Project runtime** | `state.md`, `escalations.md`, worktrees, logs (gitignored) | Yes (generated) | `runtime/<name>/` |

The engine reads **no hardcoded repo names**. Everything Plane-specific is data.

### 9.2 Directory layout (src layout, single package)
```
ash/                             # repo root
├── src/ash/                     # the ENGINE — single installable package
│   ├── api/                     # FastAPI app + routes (POST /runs, GET /runs/{id}) + lifespan
│   ├── web/                     # Jinja2 UI (dashboard, integrations, start/track runs)
│   ├── admin/                   # SQLAdmin portal at /admin + auth backend
│   ├── agents/                  # BaseAgent + intake + pm/research/coding + reviewer/fixer (stubs)
│   ├── graph/                   # state (namespaced), nodes, checkpointer, builder (conditional), runner
│   ├── integrations/            # IssueProvider + GitHub/Jira/Plane + registry/service
│   ├── db/                      # SQLAlchemy async (base/session), EncryptedString, models
│   ├── clients/                 # async github, git_repo (worktrees), pr (gh), board, code_intel
│   ├── toolkits/                # LangChain @tool wrappers (board, codebase) over clients
│   ├── llm/factory.py           # provider-agnostic chat model (Anthropic / OpenAI-compatible)
│   ├── config/                  # pydantic-settings + projects/<name>.yaml loader (hybrid)
│   ├── gates.py · schemas.py    # ApprovalGate (§7b) + Spec/Plan/CodeChange
│   ├── app_context.py           # composition root (agents → graph → Runner)
│   └── cli.py                   # thin local CLI (`ash list`, `ash run`)
├── projects/plane.yaml          # engagement config (per client/project)
├── skills/plane/SKILL.md
├── docs/plan/ (authoritative) · docs/sources/ (the source specs)
├── tests/                       # pytest + pytest-asyncio (mocked LLM/clients, MemorySaver)
├── runtime/                     # gitignored: per-project board/state/worktrees (run state = Postgres)
├── pyproject.toml · justfile · Dockerfile · docker-compose.yml · README.md
├── .github/workflows/ci.yml · .pre-commit-config.yaml
└── CLAUDE.md                    # standing self-instructions
```
The entrypoint is **FastAPI**; orchestration is **LangGraph**; run state lives in the **Postgres
checkpointer**. Quality gates: **ruff** (lint+format), **mypy --strict**, and **pytest** — all
enforced in CI.

### 9.3 Example project config (`projects/plane.yaml`)
```yaml
name: plane
issues:
  source_repo: makeplane/plane      # read-only: where issues come from
  filters: { labels: ["bug"], state: open }
work:
  target_repo: zahid-arbisoft/plane # write: branches/PRs/merges (the fork)
  base_branch: main
  upstream_remote: makeplane/plane  # for periodic sync
  open_upstream_prs: false
autonomy:
  require_human_for_merge: true     # §7b — flip to false for unattended
  require_human_for_escalation: true
budget:
  per_ticket_usd: 2.0
  per_day_usd: 20.0
schedule:
  cron: "0 9 * * *"                 # heartbeat (Phase 4)
skills: skills/plane/SKILL.md
```

Onboarding **project #2** = add `projects/foo.yaml` + `skills/foo/SKILL.md`. Run
`ash run --project foo`. Nothing in `src/` changes.

### 9.4 Documentation strategy (build it to be handed off)
Written as we build each phase, so the engine is reusable by others in the org:
- **`README.md`** — what the engine is, quickstart, the one-flag autonomy switch.
- **`docs/ONBOARD_A_PROJECT.md`** — the runbook: fork setup, token scoping, write the YAML +
  SKILL.md, first run. The repeatable "how to point this at a new repo" guide.
- **`docs/ARCHITECTURE.md`** — engine internals (graph, gates, budget, memory) for contributors.
- **`skills/<project>/SKILL.md`** — per-project context the agents load each run.
- **Inline config schema** — `projects/*.yaml` validated by a documented Pydantic model.

> Documentation milestones are added to each phase's exit criteria (see §5) so docs never lag the code.

---

## 10. The One Thing to Remember

> *"Build the loop. But build it like someone who intends to stay the engineer, not just the
> person who presses go."*

The risk isn't that the loop fails loudly — it's **comprehension debt**: it ships faster than you
understand. Every phase above keeps a human verification gate until the layer below it is trusted.
Don't remove a gate until the thing under it has earned it.

---

## 11. Changelog

Per the working agreement (top of doc), every decision/implementation change is logged here.

- **2026-06-18 — LangChain/LangGraph ecosystem usage doc added.**
  Created `docs/plan/langchain_langgraph_usage.md`: comprehensive inventory of every LangGraph
  and LangChain primitive in use (StateGraph topology, WorkflowState reducers, AsyncPostgresSaver
  checkpointer, `interrupt`/`Command` HITL gates, `aupdate_state` retry, `create_agent` ReAct
  loop, `with_structured_output`, message types, LLM factory, toolkits, MCP adapters) plus a
  pending-work section (P1 MCP tool binding, P2 Send/parallel fan-out, P3 streaming, P4
  research sinks, P5 Instructor fallback) and a quick-reference table. Companion fix: `base.py`
  now catches `LengthFinishReasonError` and falls back to `_extract`; `pm.py` skips the
  per-ticket elaborate pass and injects a COMPACT MODE note when `LLM_MAX_TOKENS ≤ 4096`.
  174 tests green, ruff + mypy --strict clean.

- **2026-06-18 — Deployment topology + code-extraction quality + Deep Agents analysis (decision #28).**
  Three topologies evaluated for running ASH against large repos (edx-platform, plane) without
  pulling code to the cloud. **Decision #28 = Topology B1** (cloud control plane + on-prem runner
  daemon): the control plane (FastAPI/LangGraph/Postgres/UI) runs on AWS; a runner daemon on the
  developer's machine holds filesystem access, worktrees, and Chroma indexing; only extracted code
  snippets go to the LLM vendor. Code never leaves the on-prem boundary. B2 (GitHub Actions) was
  rejected for HITL-loop reasons. Code-extraction quality of the current Research/Coding agents
  scored at **~38 / 100** vs Claude Code (100): strong toolkit but gaps in structural repo map
  (biggest deficit), loop depth, symbol precision, and hybrid retrieval. Improvement roadmap:
  **R1** tree-sitter repo map (+17 pts → ~55), **R2** BM25+Chroma hybrid + reranker (+8 → ~63),
  **R3** symbol index (+7 → ~70), **R4** context budget + summarization middleware (+8 → ~78).
  **Deep Agents analysis:** `langchain-deepagents` fits as the inner-loop engine for
  Research/Coding nodes (nested via `create_deep_agent`, not at macro-graph level); provides
  planning tool + sub-agent context quarantine + virtual-FS scratchpad; complements but does NOT
  replace the retrieval substrate. Sequencing: build R1 first (biggest gain, engine-agnostic),
  then swap `BaseAgent._explore` for a deepagents loop driving those tools. Pluggable coding
  engine seam documented (`ash`/`deepagents`/`aider`/`claude` per project). Implementation gated
  on Alembic migrations. Full analysis: **`docs/plan/deployment_topology_and_code_extraction_plan.md`**.

- **2026-06-17 — Default trigger flipped to `manual` (except PM) + per-agent Trigger UI.**
  `AgentPolicy.trigger` default is now **`manual`**; `ProjectConfig.agent_policy` returns `auto`
  only for agents in `DEFAULT_AUTO_TRIGGER_AGENTS` (just **PM**, so it still runs automatically to
  produce the spec). Every downstream agent (Research/Coding/Reviewer/Fixer; RFC stays opt-in) now
  waits for an explicit human trigger unless a project YAML / DB override opts it into `auto`.
  **UI:** the run page's per-story stage rows show a **manual** chip on manual stages and a live
  **▶ Trigger** button on the exact stage the graph is paused at (`pending_trigger`+`pending_story`),
  in addition to the existing top banner; a new `_agent_triggers` route helper resolves
  (DB>YAML>default) trigger modes into the timeline (`run_status`/SSE/decide render sites). The
  Agents overview + detail already reflect the resolved default. **Tests:** agent unit tests call
  `run()` outside a graph runtime, so a new `tests/agents/conftest.py` autouse fixture simulates the
  human trigger (`ash.agents.base.interrupt` → `"run"`); +2 config tests for the new default. 171
  pytest green, ruff + mypy --strict clean.

- **2026-06-17 — IMPLEMENTED: PM ticket depth via per-ticket elaboration (decision #27).** A
  comprehensive uploaded spec produced thin tickets because the whole spec was generated in one
  structured call (each ticket compressed to fit the output budget — pronounced on `gpt-4o-mini`).
  Added `PMAgent._elaborate_tickets`: after the skeleton + validation, a focused second pass
  expands each ticket with its own budget, feeding the (capped) source spec so `spec_ready` runs
  carry the provided detail verbatim. `Ticket` gained `implementation_notes`, `affected_files`,
  `api_changes`, `data_model_changes`, `out_of_scope`; rendered into `_ticket_brief`, the file Board
  (`clients/board.py`), and the PM-run detail UI. Best-effort + structure-preserving (forces
  id/type/deps back to the validated skeleton; keeps the original ticket on any per-ticket failure,
  so existing single-shot PM tests stay green). Gated by `pm_detail_tickets` (default on) +
  `pm_detail_context_chars`. +1 test. 169 pytest green, ruff + mypy --strict clean.

- **2026-06-17 — Research indexing guardrail + onboarding runbook.** (1) On a large repo the
  Research agent sat in `chroma.index_directory` (local ONNX embeddings) for minutes before its
  first LLM call — and F7's chunking made it worse (more documents). Added `INDEX_MAX_FILES`
  (default 1500): `VectorStoreClient.count_indexable` cheaply checks the file count first, and above
  the cap Research **skips semantic indexing and uses the grep fallback**; below it, indexes with a
  hard `max_files` cap + `INDEX_PROGRESS_EVERY` progress logging. Set `INDEX_MAX_FILES=0` to always
  index, `1` to force grep-only. +2 tests. (2) Wrote **`docs/ONBOARD_A_PROJECT.md`** — the
  end-to-end setup runbook (install → `.env` → infra → `projects/<name>.yaml` → connector → first
  run → troubleshooting), incl. **fork-vs-single** guidance (a fork is *not* required; `mode:
  single` for org-owned repos). 168 tests green, ruff + mypy --strict clean.

- **2026-06-17 — Fixes (decision #26 follow-up):** (1) global `input{width:100%}` legacy shim was
  stretching checkboxes/radios — added an `input[type=checkbox],input[type=radio]{width:auto;…}`
  override in `base.html`, fixing the spec-review checkboxes and the single/multiple radio
  alignment. (2) "Approve & build selected" now works: the gate is a real `<form>` of
  `name="stories"` checkboxes and `POST /ui/runs/{id}/approve` reads them into the
  `{action, stories}` decision (the prior Alpine `hx-vals js:` couldn't see Alpine scope). (3)
  Per-story run-page UI enlarged (bigger stage icons/text/padding). (4) **All app models registered
  in `/admin`**: added `SpecRecordAdmin`, `StoryRecordAdmin`, `AgentTaskAdmin`,
  `AgentRunMetricAdmin`, `AgentPolicyRecordAdmin` alongside Connector/Run/AdminUser. 166 tests green,
  ruff + mypy --strict clean.

- **2026-06-17 — IMPLEMENTED: per-story fan-out F0–F8 (decision #26).** Shipped the full
  restructure in one pass. **Engine:** `WorkflowState.stories[ticket_id]` (reducer-merged) +
  `story_order` + `current_story`; `graph/stories.py` (build/topo-sort/next-story);
  `graph/builder.py` rebuilt as `intake→(pm→pm_publish→rfc)?→plan_stories→story_router⇄story_build`
  (the build team is a compiled subgraph: research→coding→reviewer→fixer→finalize, looped
  one-story-at-a-time); `graph/nodes.py` hydrates the current story onto the flat namespaces before
  each build agent and folds the result back into `stories[…]` (agents barely changed). **Retry:**
  `Runner.retry_run` now resolves run-level failures (intake/pm/rfc, fork via `as_node`) **and**
  per-story failures/regenerate (`ticket_id`+`from_step` → reset story from step, re-enter router →
  resumes at the failed/selected story, completed stories skipped). **PM:** single (default) /
  multiple `story_mode` (form radio + prompt branch) + post-PM per-ticket selection at the review
  gate (decision may be `{action, stories}`). **No-dup PR:** deterministic per-ticket branch +
  persisted `branch`/`pr_url`; Coding updates the existing PR instead of opening a new one. **DB:**
  `StoryRecord`, `AgentRunMetric`, `AgentTask.ticket_id`, `RunRecord.story_mode` (+ PG backfills).
  **Context-min (F7):** Chroma indexes line-ranged **chunks** (not whole files); search returns
  `path:start-end` hits; `read_file(path, start, end)` line-range reads in both toolkits;
  story-scoped collection. **Analytics (F8):** `make_node` records tokens(in/out)+duration+model
  per agent/story → run-detail totals strip + per-story/stage chips, Agents-view rollups, Dashboard
  7-day KPIs. **RFC (F6):** one per run; Markdown preview (marked + DOMPurify) + raw fallback;
  collapse fix. **UI (F5):** per-story timeline cards (mini-pipeline, PR link, retry/regenerate
  buttons, review), top-right PR link/dropdown. **Quality:** 166 pytest green, ruff clean, mypy
  --strict clean (77 files). Full design + file map: `per_story_fanout_and_oversight_plan.md`.

- **2026-06-17 — PLANNED: per-story fan-out, per-story oversight, RFC/UI polish (decision #26).**
  Client brief: PM single/multiple-story toggle (single default); Dev/Research/Reviewer/Fixer run
  only when enabled and produce **one PR per story, built one by one**, with per-PR progress on the
  UI; **per-story retry** (resume from the failed story, no duplicate PRs) and **manual per-story
  regenerate** (re-research / regenerate-PR / re-review / re-fix); RFC always one per run + Markdown
  preview + collapse fix; top-right PR link(s) beside the source link. **Architecture (locked via
  client Q&A):** the story becomes the unit of execution — `WorkflowState.stories[ticket_id]`
  (reducer-merged) driven by a sequential `story_router`→`story_build` subgraph loop, modelled
  **LangGraph-first** (new non-negotiable rule in `CLAUDE.md`). Deterministic per-story branch +
  persisted `branch`/`pr_url` guarantee no duplicate PRs. Detailed design + phased build order in
  **`docs/plan/per_story_fanout_and_oversight_plan.md`**. No code yet — plan only.
  - **Brief #2 expansion (same day):** the per-story plan now also covers — **structured outputs**
    stay on LangChain primitives (`with_structured_output` / `create_agent(response_format=…)`),
    Instructor only as a documented fallback (plan §4b); **story dependency ordering** honoured by
    `story_router` (topological `story_order`); **context minimization (Research→Fixer)** — plan §12
    documents the current grounding (whole-file Chroma docs + 6000-char reads + accumulating tool
    chatter) and an improvement path (chunked index + LangChain retriever/contextual-compression +
    line-range reads + context budget + summarization middleware + story-scoped indexing; stretch:
    diff-mode `CodeChange`, AST chunking) as **F7**; **per-story worktree cleanup** at subgraph exit
    + on regenerate; **agent analytics** — new `AgentRunMetric` table capturing in/out tokens +
    duration + model (captured in `make_node`), with run/story/agent/project rollups on the UI, as
    **F8**. A better PM option added: defer single-vs-multiple to a **post-PM story selection at the
    existing review gate** (plan §4.2). Phases extended to **F0–F8**.

- **2026-06-16 (session 6) — Ticket as the unit of work + Dev runs without Research
  (decision #25).**
  - **Build a single ticket per run.** New `WorkflowState.ticket_id` (set at run start);
    `brief()` returns a focused single-ticket brief (epic context + that ticket's
    description/acceptance-criteria/deps) when set and the spec contains it, else the whole spec.
    Wired through `Runner.start_run`, `POST /ui/runs` (new `ticket_id` form field), and persisted
    on `RunRecord.ticket_id` (+ PG backfill). Run-creation form gained a "Build only this ticket"
    input; the run-detail spec view gained a **Build this ticket →** button per ticket (starts a
    fresh source-backed run scoped to that ticket id).
  - **Dev works when Research is disabled/skipped.** Extracted worktree setup into
    `agents/worktree.py::ensure_worktree` (shared by Research and Coding). Coding no longer requires
    a research plan: if `state.research` has no worktree (Research disabled, manual-not-triggered,
    or skipped) it calls `ensure_worktree` itself and builds straight from the brief (plan optional
    in `_code`). Coding records `worktree_path`/`branch` on `CodingState`; Fixer and the graph's
    worktree cleanup fall back to those when Research didn't set them. This lets a user disable the
    (still-flaky) Research agent and keep shipping PRs.
  - **Tests:** `tests/graph/test_state.py` +3 (ticket-scoped vs whole-spec brief, unknown-id
    fallback); `tests/agents/test_coding.py` +1 (coding builds with no research plan, sets up its
    own worktree, records branch). **150 pytest green, ruff + mypy --strict clean (74 files).**
  - **`_extract` uses neutral `_EXTRACT_SYSTEM` (Dev agent `json_validate_failed` fix).** The
    extraction phase (`_extract`) was reusing the agent's operational system prompt (which
    describes tool usage: "use `read_file` and `list_files` to explore..."). Groq in JSON-schema
    mode sees those instructions and outputs `{"name": "list_files", ...}` instead of the schema →
    `json_validate_failed`. Fixed by using `_EXTRACT_SYSTEM` (module constant, no tool-use
    language) as the system message in `_extract`. The operational `system` param is still
    accepted but no longer forwarded to the model. Added `test_extract_system_is_tool_free`.
    **151 pytest green.**
  - **Multi-repo Docker mount (`LOCAL_REPOS_ROOT`).** `docker-compose.yml` api volumes now include
    a second mount slot `${LOCAL_REPOS_ROOT:-/tmp}:${LOCAL_REPOS_ROOT:-/tmp}`. Set
    `LOCAL_REPOS_ROOT` in `.env` to the common parent directory of all local clones (e.g.
    `/Users/you/dev`) so every `projects/*.yaml` `work.local_repo_path` absolute value resolves
    inside the container without toggling `LOCAL_REPO_PATH` between projects.

- **2026-06-16 (session 5) — Retry-from-failed-step + hand-rolled explore loop (Groq
  `tool_choice="none"` fix).**
  - **Retry a failed run from the step that failed (decision #24).** New `Runner.retry_run(run_id,
    from_step=None, wait=False)` + `Runner.first_failed_step()`: finds the earliest namespace with
    an `error`, forks the run's checkpoint via `aupdate_state(as_node=<predecessor>)` so that step
    becomes the next node, then `ainvoke(None)` drives it (and everything after) to completion. Each
    re-run node overwrites its own namespace, so stale errors clear automatically. `POST
    /ui/runs/{id}/retry` kicks it off in the background and redirects to the run page so a fresh SSE
    stream shows live progress (the original stream closed on failure). Added a "Run failed at
    <step>" banner with a **Retry from <step>** button to `_run_timeline.html`.
  - **Fixed Groq `tool_use_failed: Tool choice is none, but model called a tool`.** `create_agent`'s
    loop sets `tool_choice="none"` on its final synthesis turn to force a text answer, but Groq's
    `gpt-oss-*` ignores that and calls a tool anyway → 400. Replaced the explore phase
    (`BaseAgent._explore`) with a **hand-rolled tool loop** that only ever uses the default
    (`auto`) tool choice: bind tools, let the model call them, feed `ToolMessage` results back, stop
    when it returns text with no tool calls (bounded by `MAX_EXPLORE_STEPS=8`). No `create_agent`,
    so the contradictory `tool_choice="none"` is never sent. Phase 2 (`_extract`) still uses
    tool-free `with_structured_output`.
  - **Tests:** `tests/graph/test_runner.py` +3 (first-failed-step + full retry-to-completion with a
    flaky agent); `tests/agents/test_base_generate.py` +1 (explore loop executes a tool call, feeds
    it back, then extracts). **146 pytest green, ruff + mypy --strict clean (73 files).**
  - **Follow-up fix:** `retry_run` now flips `status` back to `"running"` in the same
    `aupdate_state` call, so the "Run failed" banner + Retry button disappear the instant a retry
    starts (previously the reset cleared the step error while `status` stayed `"failed"`, leaving a
    stale "A step did not complete" banner mid-retry). The banner also only renders when a failed
    step is actually identified.

- **2026-06-16 (session 4) — Two-phase structured generation (Groq-safe) + RFC guardrail
  resilience.**
  - **Root cause of the research-agent `json_validate_failed` loop.** Sending **tools +
    `response_format`** in one request puts Groq's `gpt-oss-*` (via LiteLLM) into JSON-schema mode,
    so the model's tool call (`{"tool":"list_directory",…}`) gets validated against the *final*
    `ImplementationPlan` schema and the request 400s. The earlier tool-free retry was a band-aid
    that also stripped all repo grounding.
  - **Fix — `BaseAgent.generate` is now two-phase for tool-using agents:** (1) **explore** — a
    `create_agent` tool loop with **no** `response_format` (native tool-calling works normally; the
    model reads files/greps and ends with a free-text conclusion); (2) **extract** — a separate
    tool-free `with_structured_output` call that folds the exploration notes into the prompt and
    returns the typed object. Tools and the response schema are never in the same request, so the
    Groq conflict disappears *and* grounding is preserved. No-tool agents keep the single-call
    `create_agent(response_format=…)` path. New `_explore()` / `_extract()` helpers; the error
    handler now falls back to a tool-free `_extract` from the brief alone on any parse/validation
    failure. Added `tests/agents/test_base_generate.py` (2 tests) covering both paths.
  - **RFC guardrail false-positives no longer kill the run.** The LiteLLM content-safety guardrail
    blocked an RFC on a `prompt_injection_data_exfiltration` match ("expose + credentials") in the
    brief. RFC is an opt-in, non-blocking design doc, so `RFCAgent.run` now catches
    `GuardrailBlockedError` and records a skip note, letting the run continue to research/build.
  - **142 pytest green, ruff + mypy --strict clean (73 files).**

- **2026-06-16 (session 3) — Five user-reported fixes: RFC visibility, agent enable/disable,
  ONNX cache, Groq parse errors.**
  - **Agent enable/disable now actually works (two bugs).** (1) The config form's `enabled`
    checkbox used `Form(default="on")`, but an unchecked HTML checkbox submits *nothing* — so
    "off" was always read as "on". Changed to `Form(default="")`. (2) `agent_detail_view` and
    `_agent_rows` read policy from **YAML only** (`project.agent_policy`), so a saved DB override
    never showed in the UI. Both now call `resolve_policy` (DB > YAML > default); `_agent_rows`
    became async + takes a session.
  - **Single source of truth for agent policy.** Refactored `BaseAgent`: new
    `_resolve_policy(state) → (project, policy)` centralises DB>YAML>default resolution;
    `_trigger_gate(state, *, resolved=...)` reuses it. RFC previously loaded policy twice (gate
    via `base.load_project`, opt-in via `rfc.load_project`) and its opt-in guard checked `trigger`
    but **not** `enabled` — so a disabled RFC still ran. RFC now resolves once and checks the
    opt-in (`trigger==auto`) *before* the gate (RFC has no manual-trigger UI, so `!=auto` = skip,
    never an interrupt).
  - **RFC is now findable + visible (was generated into the void).** The RFC agent now writes the
    Markdown to `<runtime_dir>/rfc/<run_id>.md` and carries `doc_ref`/`title` in `RFCState`. Added
    an "RFC — design doc" stage to the run-timeline pipeline and a collapsible RFC section showing
    the title, file/URL ref, and full document.
  - **ONNX embedding model no longer re-downloads (~79 MB each restart).** Chroma's default
    embedding function runs **client-side** — i.e. in the ASH `api` container, not the chroma
    server. The cache volume was mounted on the wrong service; moved
    `chroma-onnx:/root/.cache/chroma` to the `api` service.
  - **Groq `output_parse_failed` now caught for retry.** The model sometimes emits free-text
    reasoning where the ReAct/structured-output parser expects a tool call or final JSON; added
    `output_parse_failed` / `parsing failed` / `could not be parsed` to `generate()`'s
    direct-structured-output fallback condition so the research agent degrades instead of crashing.
  - **140 pytest green, ruff + mypy --strict clean (73 files).** Updated `test_rfc.py` (patches
    `ash.agents.base.load_project`, autouse fixture forces the DB-down fallback for hermetic unit
    tests) and `test_routes_helpers.py` (`_agent_rows` now async with an in-memory SQLite session).

- **2026-06-16 (session 2) — Token efficiency, reliability, and config fixes.**
  - **Per-agent LLM routing (base_url + api_key overrides):** `AgentModelOverride` gained
    `base_url` and `api_key` fields; `settings.effective_api_key(agent)` returns the per-agent key
    if set, falling back to the provider default. `AGENT_REVIEWER__BASE_URL` /
    `AGENT_REVIEWER__API_KEY` route the Reviewer to Google AI Studio without touching other agents.
    `env_file` changed to an absolute path (`REPO_ROOT / ".env"`) so it's CWD-immune.
  - **Token efficiency — no more full context dumps:** Research agent no longer pre-dumps the repo
    tree into the prompt; the agent explores via `list_directory` → `grep_code` → `read_file` tools
    (Claude Code–style). Coding agent similarly removed the pre-loaded tree + initial file contents.
    Reviewer agent truncates each changed file to 4 000 chars before sending to the LLM.
  - **`_trigger_gate` async + enabled check + DB overrides:** `BaseAgent._trigger_gate` made async;
    now calls `resolve_policy(session, project, agent_name)` to merge DB override > YAML > default.
    Checks `policy.enabled` before the trigger check — disabled agents now self-skip correctly.
    All four call sites updated (research, coding, reviewer, fixer).
  - **RFC enable/disable fixed:** RFC agent now calls `await self._trigger_gate(state)` (picks up DB
    overrides) then an extra `resolve_policy` check for `trigger != "auto"` to enforce opt-in
    semantics. Enabling/disabling from the UI now works correctly.
  - **Groq retry path:** `generate()` retry (on tool-validation / json_validate_failed errors) now
    calls `model.with_structured_output(schema).ainvoke(messages)` directly — bypasses `create_agent`
    entirely, avoiding Groq's strict tool-schema enforcement on the retry.
  - **LangGraph msgpack allowlist complete:** `checkpointer.py` now registers all ASH Pydantic
    models (`Epic`, `TechnicalSpec`, `Ticket`, `Risk`, `FileEdit`, `ReviewFinding`, `RFCDocument`)
    and all `str, Enum` types (`TicketType`, `Severity`, `EditAction`, `ReviewSeverity`,
    `ReviewVerdict`). Deserialization warnings eliminated.
  - **Chroma ONNX model cache:** `docker-compose.yml` gained a `chroma-onnx` named volume mounted
    at `/root/.cache/chroma` in the Chroma service so the embedding model persists across restarts.
  - **Agent "in_progress" status in UI:** `_run_timeline.html` stage macro accepts an `agent_key`
    param and shows a pulsing ◉ indicator when the DB `AgentTask` status is `in_progress`. Routes
    and SSE generator pass `task_statuses` dict on every render tick.
  - **Claude Code–style harness added as future capability** to Phase 5 / backlog (§5).
  - **mypy --strict clean: 73 source files.**

- **2026-06-16 — Agent task dispatch system (D1–D4 + UI1): per-agent task queue, background
  dispatcher, and agent detail pages.**
  **D1 (DB models):** `AgentTask` table (status: pending/in_progress/completed/failed/cancelled/
  scheduled, retry tracking, result_ref, timestamps) + `AgentPolicyRecord` table (DB overrides for
  trigger/enabled/concurrency_limit/daily_quota/max_retries/schedule_cron). `AgentPolicy` in
  `settings.py` extended with the new dispatch fields. Full CRUD in `db/tasks.py`.
  **D2 (HITL disambiguation):** `Runner.get_run` now distinguishes three interrupt types
  (`spec_review` / `manual_trigger` / `merge_approval`) and sets `pending_review` / `pending_trigger`
  / `pending_merge` flags. `_run_timeline.html` gained two new HITL gate blocks (trigger + merge).
  Approvals page shows colour-coded gate-type badges.
  **D3 (task lifecycle in pipeline):** `make_node` in `graph/nodes.py` accepts `node_name`; each node
  creates/updates `AgentTask` rows as best-effort side-effects. Mapping: intake→creates pm task;
  pm_publish→completes pm task + creates research task; rfc/research/coding/reviewer/fixer each
  complete their own task and create the next one. `builder.py` passes `node_name` to each
  `make_node` call.
  **D4 (background dispatcher):** `graph/dispatcher.py` — `DispatchService.tick()` scans all pending
  tasks every 10 s, respects trigger='auto', concurrency_limit, daily_quota, schedule_cron
  (croniter), max_retries; resumes runs via `Runner.resume_run(run_id, "run")`. Wired into FastAPI
  lifespan in `api/app.py`. `croniter>=2.0` added to dependencies.
  **UI1 (agent detail pages):** `/ui/agents/{name}` — per-agent page with stats bar (pending /
  in_progress / completed / failed / success_rate), filterable task list (Trigger/Cancel buttons per
  row), and an editable config panel saving to `AgentPolicyRecord` (DB override) with a reset-to-YAML
  button. Agents overview cards annotated with pending/running badges; cards link to detail pages.
  New routes: `GET /ui/agents/{name}`, `POST /ui/agents/{name}/config`, `POST
  /ui/agents/{name}/config/reset`, `POST /ui/tasks/{id}/trigger`, `POST /ui/tasks/{id}/cancel`.
  **Also fixed (same session):** `.env` had a mangled line where `LOCAL_REPO_PATH` was appended to
  `POSTGRES_DSN` without newline, leaving it empty and causing Research/Spike to skip despite
  `local_repo_path` being set in plane.yaml. `docker-compose.yml` got a bind-mount
  `${LOCAL_REPO_PATH:-/tmp}:${LOCAL_REPO_PATH:-/tmp}` so the host clone is visible inside the
  container.
  **Run timeline now shows "running" state:** `_run_timeline.html` `stage()` macro gained an
  `agent_key` param; routes pass `task_statuses` (agent_name→status dict from `list_tasks_for_run`)
  to all timeline render paths (SSE stream, initial load, approve/reject/trigger responses).
  When `AgentTask.status == "in_progress"` and the agent has no output yet, the stage shows a
  pulsing ◉ "running" indicator instead of jumping directly from "pending" to "done".
  Verified: ruff + mypy --strict clean (**73 source files**), **140 tests green**.

- **2026-06-15 — Research sinks (A5) + connectors page port.** The Research agent now publishes its
  output: `agents/research_doc.py` renders the `ImplementationPlan` to Markdown and
  `publish_research_doc()` writes it to a local file (default), posts it as a comment on the source
  connector's issue, or skips — selected by `ProjectConfig.research_sink` (`file`/`comment`/`none`);
  `ResearchState.doc_ref` records where it went; publishing is best-effort. Connectors UI page ported
  to the design system with per-row **MCP/built-in** + role badges and an inline **validation status**
  (from `validate_connector`) showing incoherent rows, plus a concise setup guide. Verified: ruff +
  mypy --strict clean (69 files), **110 tests green** (+5 research-doc).

- **2026-06-15 — Work board + connector coherence checks + startup column backfill.** Added the
  **Work board** (`/ui/work`, `work_board.html`): recent runs as cards in status columns
  (In progress / Awaiting review / Done / Failed); promoted from the sidebar's "soon" list.
  **Connector coherence (C1 slice):** `integrations/service.validate_connector` enforces the role/MCP
  "together checks" the brief asked for — `is_default_sink ⇒ is_sink`, MCP (`transport='http'`) ⇒
  `base_url`, and source/sink **kind-capability** (source ∈ github/jira/plane, sink ∈ file/jira/plane,
  matching `registry`/`build_sink`); `create_connector` now takes `transport` and raises `ValueError`
  on an incoherent combo. **Migration shim:** `init_db()` runs idempotent `ADD COLUMN IF NOT EXISTS`
  backfills (Postgres) so DBs predating the schema change get `run_records.status`/`task_sink_id` on
  startup — fixed a live `column run_records.task_sink_id does not exist` 500. Verified: ruff + mypy
  --strict clean, **105 tests green** (+7 validation), all templates render offline, routes register.

- **2026-06-15 — Reviewer + Fixer agents built, spec persistence, per-agent trigger config, and the
  Jira-style UI (U0–U3) shipped.** Executed the §13 roadmap's first block.
  **Agents:** `ReviewerAgent` (was a stub) now does a single deep `create_agent` review →
  `CodeReview` (new schema: `ReviewSeverity` nit/low/medium/high/critical, `ReviewFinding`,
  `ReviewVerdict`), posts the review via `gh` when a PR exists, and merges only when
  `autonomy.auto_merge_on_approve` is set **and** the merge gate allows it (else awaits a human).
  `FixerAgent` (was a stub) reuses the Dev agent's apply/commit/push on the same worktree to address
  blocking findings, bounded by `MAX_FIX_ITERATIONS`, and refreshes the PR description. Worktree
  cleanup moved from Coding to the `merge` node so the worktree survives for Reviewer/Fixer. Coding's
  `_apply_change` is now the shared public `apply_change`; commits/PR titles are ticket-number
  prefixed. `pr.py` gained `comment_pr`/`review_pr`/`edit_pr_body`/`merge_pr`.
  **Persistence (A0):** new `SpecRecord` table (one spec per run, denormalized title/summary for
  search) written best-effort by PM; `RunRecord` gained `task_sink_id` + `status`; `Runner` syncs the
  final status onto the record. `db/runs.py` adds `persist_spec_record`/`update_spec_ticket_refs`/
  `update_run_status`/`search_spec_records`.
  **Config (A4):** `AgentPolicy{trigger: auto|manual, enabled}` + `agents:` map on `ProjectConfig`
  (`agent_policy()` helper); `Autonomy.auto_merge_on_approve`; `KNOWN_AGENTS`. Auto-trigger *dispatch*
  is still TODO — only the policy/UI surface landed.
  **UI (U0–U3):** rebuilt the shell to a Jira-style sidebar+topbar on **Tailwind + HTMX + Alpine**
  (CDN); new pages — live **run detail** (SSE `/ui/runs/{id}/events` + htmx swap, inline Approve/Reject
  gate posting to `/resume`), **Approvals** queue, searchable+paginated **PM runs** (+ spec detail),
  and **Agents** overview (trigger/enabled/model/HITL per project, RFC placeholder). Dashboard, runs
  list, and new-run form ported to the design system; a legacy-class shim covers the still-bespoke
  connectors page. Verified: ruff + mypy --strict clean (src), **98 tests green** (+10), all templates
  render offline, app constructs and routes register. Live Postgres/LLM/`gh` paths still need real
  credentials. **Schema migration:** `init_db()` now runs idempotent `ADD COLUMN IF NOT EXISTS`
  backfills (Postgres only, `db/base.py`) so DBs predating this change pick up the new
  `run_records.status`/`task_sink_id` columns on startup (the `spec_records` table is created by
  `create_all`) — fixes the `column run_records.task_sink_id does not exist` 500. Proper Alembic
  still pending. **Remaining (next):** C1 connectors overhaul, A1 Dev tool-loop (P2), A4 auto-dispatch,
  A5 research sinks, P4b HITL middleware, Work-board view, RFC.

- **2026-06-15 — Full team spec + connector overhaul + Jira-style UI plan (decisions #22, #23).**
  Captured the client brief for the whole agent roster (PM/Research/Dev/Reviewer/Fixer/RFC) with
  per-agent inputs, destinations, tools, response schemas, HITL gates, and a new cross-cutting
  **trigger mode** (`auto`/`manual`) requirement — written into `agent_runtime_and_connectors_plan.md`
  §10. Added a **connector overhaul** plan (§11): MCP-first with legacy httpx as visible backup,
  discriminated per-kind Pydantic config (no free-form JSON), coherent role/MCP validators, connection
  health check, `task_sink_id` persisted on `RunRecord`. Added a **Jira-style UI overhaul** plan (§12):
  server-rendered **HTMX + Tailwind + Alpine** (decided over a React SPA — keeps one Python codebase,
  no Node build target), sidebar+topbar IA, shared design-system macros replacing inline CSS, SSE-driven
  live run status, dedicated PM-runs (searchable/paginated), Agents, Connectors, and Approvals sections.
  Added the **execution roadmap** (§13): chosen order is **UI foundation first → agents → connectors,
  interleaved** (U0–U3, C1, A0–A5, P4b, RFC last), folding the old §5 P2–P6 into it. Build started this
  turn at U0 (UI foundation). No agent-loop behaviour changed by the planning pass.

- **2026-06-15 — Spec-quality rule tightening (second pass).** Re-ran the same requirement through
  PM post-hardening; the structural issues (cycles, dep graph) were fixed, but four prompt-level gaps
  remained: (a) named restrictions ("no screen recording", "no external calls") missing from epic AC
  as negative conditions; (b) "working prototype" still got 3-OS signal adapters; (c) `open_questions`
  field left empty despite Workstream format and UI framework being undefined; (d) activity-monitoring
  consent/labor-law risk absent. **Fixes:** Rule 2 strengthened (named restrictions → negative AC
  conditions); Rule 3 tightened (prototype targets ONE platform unless multi-platform is explicit);
  Rule 4 expanded with mandatory pre-finish audit and a note that empty `open_questions` on a
  greenfield project is a red flag; Rule 6 extended with an explicit employment-monitoring consent
  clause (GDPR Art. 13, CCPA, local labor law). `Spec.open_questions` field description updated to
  list concrete triggers (undefined format, undecided framework/platform). `docs/best_practices.md`
  updated with the new rule text and a second worked-example table mapping Run-2 defects to the new
  enforcement. Verified: ruff + mypy --strict clean, 88 tests green (unchanged).

- **2026-06-15 — Spec-quality hardening (prompt rules + deterministic validation) + Arbisoft
  best-practices integration (decision #21).** Diagnosed real `raw_to_spec` output that shipped six
  defects: a circular dependency (T2↔T3), an invented tech stack ("existing Electron/React app"),
  a dropped UX reference (Granola), prototype scoped as a production system, an undefined external
  format assumed silently, and an incomplete risk assessment. **Two-layer fix:** (1) `_QUALITY_RULES`
  appended to both PM system prompts (`raw_to_spec` + `spec_ready`) — six hard rules + a self-check;
  `Ticket.title`/`description` now carry a cold-start-executable quality bar, and `Spec.open_questions`
  gives the model a place to record unknowns instead of inventing them. (2) New
  `agents/spec_validator.py` (`validate_spec`) deterministically checks the dependency graph is
  acyclic (DFS cycle detection), all deps reference real ticket ids, no self-deps, unique ids, and
  spike↔needs_research consistency. `PMAgent._validate_and_repair` runs one self-correction round on
  failure (errors fed back → regenerate); residual issues are surfaced in `open_questions` for the
  human review gate. **Integration:** vendored `ai-first-engineering`, `blueprint`, and
  `prompt-optimizer` skills verbatim into `.claude/skills/` (provenance noted) and documented the
  standard + how ASH enforces it in `docs/best_practices.md` (linked from README). Verified: ruff +
  mypy --strict clean (67 files), 88 tests green (+10: 8 validator, 2 PM correction-loop).

- **2026-06-15 — Connector config hints in admin + interactive setup guide on connectors page.**
  Added per-field help text (`column_descriptions`) to `ConnectorAdmin` in `admin/views.py` for the
  `config`, `secret`, `base_url`, `transport`, `is_source`, `is_sink`, and `is_default_sink` fields —
  each shows kind-specific examples (e.g. `github: {"repo": "owner/name"}`) and where to obtain the
  API token. Added a full "Setup guide" card to `templates/connectors.html` — interactive kind-selector
  tabs (GitHub / Jira / Plane) each showing a copyable JSON config template, field reference table
  (required vs optional badges, descriptions), secret/base_url notes, and a GitHub-specific callout
  explaining multiple-repo support (each connector row is independent; select per run). No code changes
  required for multi-repo support — the architecture already supports N same-kind connectors.
  Verified: ruff + mypy --strict clean, 71 tests green.

- **2026-06-15 — Intake fail-fast + GitHub connector setup guide + cascade error fix.**
  Diagnosed: selecting no connector + an item ID with a project YAML that has no `source_repo`
  caused intake to build a `GitHubIssueProvider` with an empty `repo`, producing a cryptic
  `404 GET /repos//issues/...` error that then cascaded through PM and pm_publish.
  **Three root fixes:** (1) `GitHubIssueProvider.fetch_issue` validates `repo` before any network
  call (raises `ValueError` with a clear "set repo in connector config" message). (2) `IntakeAgent._resolve`
  validates `project.issues.source_repo` is non-empty and `GITHUB_TOKEN` is set before using the
  legacy project-YAML path; raises an actionable error listing all three fix options. (3) Graph
  routing after intake: if `state.intake.error` is set, route directly to `merge` (fail fast) —
  PM and research no longer run when intake couldn't fetch the issue. **pm_publish** also updated:
  if `state.pm.spec is None`, return `{}` (no-op, preserve PM's error) instead of emitting a
  second misleading error. **Run form** updated with a "No connectors yet" setup guide, an
  inline JS warning when an item ID is entered with no connector selected, and clearer help text.
  Two new tests: `test_graph_fails_fast_on_intake_error`, `test_intake_error_skips_pm_and_fails_run`.
  Verified: ruff + mypy --strict clean (65 files), 71 tests green.

- **2026-06-15 — Intake mode semantics corrected + UI review gate + runs list page.**
  Three changes shipped together:

  1. **Intake mode semantics (decision #20):** Clarified and implemented the intended behaviour
     for all three modes:
     - `raw_to_spec` — PM receives raw requirements (issue text / uploaded docs) and generates a
       full spec **and** implementation tickets (stories). Human review gate before push.
     - `spec_ready` — PM receives a pre-written specification and extracts implementation tickets
       from it without re-writing the spec from scratch. Uses a distinct `_SYSTEM_SPEC_READY` prompt
       ("stay faithful to the spec, break into stories"). Human review gate before push.
     - `raw_to_dev` — PM is skipped entirely; the build team works directly from the raw issue.
     **Breaking change from old behaviour:** `spec_ready` previously tried to parse the issue body
     as a raw JSON `Spec` object (brittle; failed for real natural-language specs) and skipped PM
     entirely. Now `spec_ready` routes **through PM** — the conditional edge in `builder.py` skips
     PM only for `raw_to_dev`. The JSON-parsing shortcut in `intake.py` is removed.

  2. **PM two-phase design + HITL review gate (from 2026-06-15 session):** PM is split into two
     graph nodes: `pm` (spec/ticket generation → board write → checkpoint) and `pm_publish` (calls
     `langgraph.types.interrupt("spec_review")` → pauses → user reviews spec in the UI → Approve
     or Reject → tickets pushed to connector on approval). `graph/nodes.py` updated to re-raise
     `GraphInterrupt` instead of catching it. `runner.get_run()` overlays `status="awaiting_review"`
     and `pending_review=True` when `snapshot.interrupts` is non-empty.

  3. **Pretty spec view (`run_status.html`):** Replaced raw JSON dump with a tabbed card layout —
     Overview (epic + acceptance criteria), Technical (approach, affected areas, data/API changes),
     Tickets (card grid with type badge, SPIKE flag, estimate, deps), Risks (severity-coloured rows).
     Auto-refresh only while `status == "running"`; stops during review gate so the user can interact.

  4. **Paginated runs list (`/ui/runs`):** New route + `runs_list.html` template — table of all
     runs with project/mode filters and pagination (20 per page). "Runs" added to the nav.
     Run-new form descriptions updated to match the corrected mode semantics.

  Verified: ruff + mypy --strict clean (65 files), 69 tests green.

- **2026-06-12 — MCP connector layer (P3, hosted HTTP) — access GitHub/Jira/Plane via their MCP
  servers.** Added `Connector.transport` (`http` = a hosted MCP server; blank = the built-in httpx
  client, kept as fallback per decision). `integrations/mcp.py` builds a `MultiServerMCPClient`
  StreamableHttp connection from the connector (`base_url` + `Authorization: Bearer <secret>` /
  `config.headers`) and `load_mcp_tools()` / `service.mcp_tools_for(id)` return the server's tools as
  LangChain `BaseTool`s; admin + UI expose `transport`. Tested: config building, mocked tool-load,
  and an agent calling an MCP tool through `create_agent`. **Remaining:** bind a run's connector MCP
  tools into the live agents (pairs with P2) + verify against a real hosted server (can't run MCP
  servers in CI). Verified: ruff + mypy --strict clean (66 files), 67 tests green.

- **2026-06-12 — Adopted `create_agent` runtime (P0+P1) + HITL resume mechanism (P4) from the
  agent-runtime plan.** Added `langchain` 1.3.8 + `langchain-mcp-adapters` 0.3.0. **P1:**
  `BaseAgent.build_agent()` constructs a LangChain `create_agent` (model + tools + `system_prompt`
  + `response_format`); `generate()` runs it and returns the validated object — every structured
  agent (PM/Research/Coding) now shares one maintained agent runtime instead of a hand-rolled
  structured-output call. **P4 mechanism:** `Runner.resume_run` + `POST /runs/{id}/resume`, with a
  test proving interrupt→resume through the checkpointer (the P0 risk-#1 item). **Still TODO:** P2
  (give the looping agents real tools so `create_agent` loops), P3 (MCP connector layer — needs live
  `uvx`/hosted servers to verify), P4 middleware activation (`HumanInTheLoopMiddleware` on dangerous
  tools + `Autonomy`→`interrupt_on` + UI approve/reject), P5/P6. Verified: ruff + mypy --strict clean
  (66 files), 61 tests green.

- **2026-06-12 — Unified `Integration` + `TaskSink` → one `Connector` model.** Collapsed the two
  overlapping tables into a single `connectors` table: a connector has `is_source` / `is_sink`
  (a system like Jira is configured **once** for both directions) + `is_default_sink`, replacing
  separate `ProviderKind`/`SinkKind` with one `ConnectorKind` (github/jira/plane/file/sheets). Updated
  the source registry/service (`build_provider`, `provider_for` checks `is_source`, `create_connector`/
  `list_connectors`/`get_connector`), the sink service (`resolve_task_sink`/`get_default_sink`/
  `build_sink`), a single `ConnectorAdmin` at `/admin`, and the UI (`/ui/connectors`; run form lists
  sources vs sinks by role). Run/state fields keep the names `integration_id` (source) /
  `task_sink_id` (sink) but now reference connector ids. **No Alembic** → existing `integrations`/
  `task_sinks` rows are re-entered as connectors. Verified: ruff + mypy --strict clean (65 files),
  60 tests green.

- **2026-06-12 — Dockerfile dep-layer caching + PM-only config robustness.** (1) Dockerfile split so
  third-party deps install from a stub package keyed only on `pyproject.toml`; the real package is an
  `--no-deps -e .` editable install — code edits no longer trigger a full dependency reinstall (and
  with the `.:/app` bind mount, code changes need only `docker compose restart api`, not a rebuild).
  (2) `ProjectConfig.issues`/`work` are now **optional** so a `name`-only project supports PM-only /
  attachments runs; Research/Coding/intake/CLI guard the `None` cases. (3) `/ui/runs` form handler
  defaults `item_id`/`intake_mode` so a blank item id can't 422. ruff + mypy --strict clean, 59 tests.

- **2026-06-12 — PM agent v2 implemented (file ingestion, task sinks, spikes, uploads).** Shipped
  the PM v2 behavior from §8b of the runtime plan (the create_agent/MCP migration remains next):
  - **File ingestion:** `documents/reader.py` reads uploaded specs (md/txt/pdf/docx/html) via
    LangChain community loaders (`langchain-community` + `pypdf` + `docx2txt`). PM ingests issue text
    **and/or** attachments.
  - **Task sinks:** new `TaskSink` DB model (kind file/jira/plane/sheets, encrypted secret,
    `is_default`, enabled) + `sinks/` backends — `FileBoardSink` (default), `JiraTaskSink`,
    `PlaneTaskSink` (httpx create-issue). Per-run selection: explicit `task_sink_id` → admin default
    → local file board (`sinks/service.resolve_task_sink`); new admin `TaskSink` view.
  - **PM v2:** raw/attachments → `Spec` (board) → **tickets pushed to the selected sink**
    (`ticket_refs` recorded). Spikes: `TicketType.spike` + `Ticket.needs_research`; PM flags them and
    notes them for Research (which already consumes the spec).
  - **Uploads:** `POST /uploads` (multipart) + UI file picker + sink dropdown; `WorkflowState`/
    `RunRequest` gain `attachments` + `task_sink_id`; intake skips remote fetch for attachment-only
    runs. **MCP-backed sinks + create_agent + HITL still pending** (next phase).
  - Verified: ruff + mypy --strict clean (65 files), **58 tests green**. Live Jira/Plane pushes
    pending real creds.

- **2026-06-12 — Plan (no code): adopt `create_agent` + middleware + MCP connectors.** Wrote
  [`docs/plan/agent_runtime_and_connectors_plan.md`](agent_runtime_and_connectors_plan.md): upgrade
  to LangChain 1.x (≥1.1) + `langchain-mcp-adapters`; make every agent (incl. a minimal PM) a nested
  `create_agent` inside the orchestration graph; back `ApprovalGate` with `HumanInTheLoopMiddleware`
  + a `POST /runs/{id}/resume` endpoint; replace bespoke GitHub/Jira/Plane providers with **MCP
  servers** (reframing the `Integration` registry → `Connector`, reusing DB/encryption/admin/UI);
  `deepagents` deferred. Phased migration P0–P6; implementation pending sign-off.
- **2026-06-12 — Plan extended: PM agent v2 (§8b of the runtime plan).** PM grows to: (R1) ingest
  requirements from uploaded files (pdf/md/doc/…) via a `documents` reader (`markitdown`); (R2) two
  modes — raw→spec, and spec→tickets pushed to the user's task tool (Plane/Jira/Google Sheet) via a
  `TaskSink` (default file board; Plane/Jira via MCP write tools); (R3) mark tickets as **spikes**
  (`TicketType.spike` + `Ticket.needs_research`) handed to the Research agent. Forks (push
  mechanism, Sheets scope, upload mechanism, sequencing) being confirmed before code.

- **2026-06-11 — Fix: board specs invisible on host (docker-compose runtime volume).** A named
  volume `ash-runtime:/app/runtime` shadowed the project bind mount, so PM board files
  (`runtime/<proj>/board/issue-*.md`) were written into the Docker volume, not the host `./runtime`.
  Dropped the named volume; `runtime/` now lives under the `.:/app` bind mount and is visible on the
  host. (Recreate the container — `docker compose up -d` — for the volume change to take effect.)

- **2026-06-11 — Fix: `TypeError: Object of type Spec is not JSON serializable` on run status.** Once
  PM produced a real `Spec`, LangGraph handed `get_run` a namespace dict still wrapping the `Spec`
  object; the previous normalizer only `model_dump()`ed top-level pydantic values, so the `Spec`
  leaked into `json.dumps` (UI `tojson` / API response). `Runner.get_run` now deep-converts via
  `pydantic_core.to_jsonable_python` (enums→values, datetimes→iso, models→dicts), guaranteeing a
  JSON-safe payload. Added a regression test. ruff + mypy --strict clean, 46 tests green.

- **2026-06-11 — Docs: `docs/configuration.md` env/model reference.** Complete table of every env var
  (LLM provider/model/keys/base_url, per-agent `AGENT_*__MODEL` overrides, DB, `SECRET_KEY`, admin,
  `LOCAL_REPO_PATH`, `ASH_ROOT`), how the per-agent model is resolved, working `.env` examples
  (LiteLLM gateway / native Anthropic / per-agent), and a troubleshooting checklist mapping the
  common gateway errors (missing key, `token_not_found`, `key_model_access_denied`) to fixes. Linked
  from the README. Also: openai/gateway path sends a placeholder key when none is set (gateways that
  don't check keys), and uses `max_tokens` explicitly to avoid a runtime warning.

- **2026-06-11 — Fix: LLM env vars are flat (`LLM_PROVIDER`, not `LLM__PROVIDER`).** The global LLM
  config was nested under an `llm` object, so `LLM_PROVIDER` (single underscore) was silently ignored
  and the provider fell back to `anthropic` (causing a confusing "Could not resolve authentication
  method" / `ANTHROPIC_API_KEY` error even with `LLM_PROVIDER=openai` set). Flattened to
  `llm_provider`/`llm_model`/`llm_temperature`/`llm_max_tokens` (read from `LLM_PROVIDER`/`LLM_MODEL`/
  …); per-agent overrides stay nested (`AGENT_PM__MODEL`). Also: clearer "no LLM credentials" errors
  in the factory, and Research now **skips** (clean note) when the configured clone path is
  missing/not mounted instead of raising `FileNotFoundError`. Updated `.env.example`, README, CLAUDE,
  tests (hermetic via `_env_file=None`). ruff + mypy --strict clean, 45 tests green.

- **2026-06-11 — Docs: `docs/integrations.md` how-to.** Step-by-step guide for adding GitHub / Jira /
  Plane integrations (prereqs, admin-portal fields per provider, token scopes, item-id meaning,
  intake modes, programmatic seeding, security & troubleshooting). Linked from the README.

- **2026-06-11 — Fix: admin "add integration" crash (sqladmin/wtforms boolean widget).** Rendering
  the `enabled` checkbox raised `AttributeError: 'BooleanInputWidget' object has no attribute
  'validation_attrs'` — sqladmin 0.27.2's `BooleanInputWidget` subclasses wtforms' base `Input`, but
  wtforms ≥3.2 defines `validation_attrs` only on concrete subclasses. Added `admin/_compat.py` (a
  no-op-once-fixed shim that restores the attribute), imported by `ash.admin`. Regression test
  reproduces the crash and verifies the fix. ruff + mypy --strict clean (57 files), 44 tests green.

- **2026-06-11 — DB-backed admin users (create via CLI/justfile).** Added an `AdminUser` table
  (PBKDF2-SHA256 hashes, stdlib — no new deps) + `admin/security.py` (`hash_password`/
  `verify_password`) + `admin/users.py` (create-or-update / authenticate). `AdminAuth.login` now
  checks DB users first, falling back to the env `ADMIN_USER`/`ADMIN_PASSWORD` bootstrap user. New
  `ash create-admin --username … [--password …]` CLI (prompts via getpass when omitted) and a
  `just create-admin <user>` recipe. Read-only `AdminUser` view added to the portal (creation stays
  in the CLI so passwords are always hashed). Verified: ruff + mypy --strict clean (56 files), 43
  tests green (hash roundtrip + create/authenticate over sqlite).

- **2026-06-11 — Integrations, admin portal & Jinja2 UI (decision #19).** Added a pluggable
  **issue-source integrations** layer + an app DB + a server-rendered UI + an admin portal.
  - **App DB (SQLAlchemy 2.0 async):** new `src/ash/db` (`base` engine/sessionmaker from the same
    Postgres DSN via `postgresql+psycopg`, `init_db` create_all, `crypto.EncryptedString` Fernet
    column type, `models`: `Integration` + `RunRecord`). Tables created on startup (Alembic is a
    later hardening step). Integration **secrets are encrypted at rest** (Fernet key from
    `Settings.secret_key`).
  - **Integrations (`src/ash/integrations`):** `IssueProvider` protocol + normalized `RawIssue`;
    full **GitHub / Jira / Plane** providers over httpx (Jira ADF→text, Basic auth; Plane
    `X-API-Key`); `registry` (row→provider) + `service` (CRUD + `provider_for`). New sources = new
    provider + `ProviderKind`, no agent/graph changes (realizes the §8 Trigger seam).
  - **Intake routing (per-run, decision #19):** new **IntakeAgent** front node resolves the
    integration (or legacy GitHub fallback) → `RawIssue`. `WorkflowState` gains `intake_mode`
    (`raw_to_spec | spec_ready | raw_to_dev`), `integration_id`, `raw_issue`, and a `brief()` helper.
    A LangGraph **conditional edge** routes: `raw_to_spec`→PM→build; `spec_ready` (spec parsed at
    intake) and `raw_to_dev`→build, skipping PM. PM now consumes `raw_issue`; Research/Coding work
    from `brief()` (spec or raw). Board sink keys on a string item id (Jira keys, etc.).
  - **Admin + UI:** **SQLAdmin** at `/admin` (Integration + Run views) behind an env-credentialed
    `AuthenticationBackend`; **Jinja2** UI at `/` (dashboard, integrations list, start-run form with
    integration + intake-mode pickers, run status). API `RunRequest` gains `intake_mode` +
    `integration_id`; lifespan runs `init_db()` + mounts admin. New deps: sqlalchemy[asyncio],
    sqladmin, cryptography, jinja2, python-multipart, itsdangerous (+ aiosqlite dev).
  - **Verified:** ruff clean, **mypy --strict clean (54 files)**, **39 pytest tests green** (provider
    parsing via httpx MockTransport, Fernet encryption-at-rest over sqlite, intake conditional
    routing for all three modes, app/UI/admin import). Live Postgres/Jira/Plane runs pending real
    credentials. **Follow-ups:** Alembic migrations (tables are `create_all` today), per-integration
    comment-back wired into a node, real Reviewer/Fixer.

- **2026-06-11 — Re-architecture to the boilerplate-spec stack (FastAPI + async + LangGraph +
  Postgres + LangChain).** Adopted the AI-PM-Engineering boilerplate spec (now under
  `docs/sources/`) while keeping ASH's agent roster and Board/PR separation. Decisions #15–#18 added;
  **#14 (Django control plane) superseded and removed.**
  - **Layout:** moved the package to a `src/` single-package layout (`src/ash/`); removed Django
    (`apps/`, `config/`, `manage.py`). New sub-packages: `api/` (FastAPI), `graph/` (LangGraph),
    `clients/` (async github + moved git_repo/pr/board/code_intel), `toolkits/` (`BaseTool` wrappers),
    `llm/` (factory), `config/` (settings package). Removed `pipeline.py`, `main.py`, flat `state.py`,
    hand-rolled `llm.py`/`config.py`.
  - **Orchestration:** LangGraph `StateGraph` PM→Research→Coding→Reviewer→Fixer→Merge over a
    **namespaced** `WorkflowState` (per-agent sub-states); node adapter captures errors per namespace;
    merge sets terminal status. **AsyncPostgresSaver** = run state of record, keyed on `run_id`.
  - **Entry:** FastAPI `POST /runs` (background task) + `GET /runs/{id}` (checkpointer read) +
    `/health`; lifespan opens/setups the checkpointer. Thin `ash` CLI (`list`/`run`) for local runs
    (MemorySaver, no Postgres).
  - **LLM:** provider-agnostic **LangChain** factory (`ChatAnthropic`/`ChatOpenAI`, `base_url` for
    LiteLLM/Ollama/vLLM); agents force structure via `.with_structured_output`. Engine is fully
    **async** (blocking git/subprocess via `asyncio.to_thread`).
  - **Agents:** ported PM/Research/Coding to async `BaseAgent`; Reviewer/Fixer are stubs. With no
    local clone, Research/Coding **skip gracefully** so a PM-only run completes (bridges both specs).
  - **Config:** **hybrid** — `pydantic-settings` `Settings` (secrets + per-agent overrides) + retained
    `projects/<name>.yaml` (design rule #1 preserved).
  - **Tooling/infra:** Python ≥3.12; deps swapped to langchain/langgraph/fastapi/psycopg/httpx;
    docker-compose now runs **Postgres** (+ api); **mypy --strict** + ruff + pytest-asyncio enforced
    in **GitHub Actions CI** + pre-commit. Plan renamed → `ash_architecture_and_plan.md`; the 3 source
    specs moved to `docs/sources/`.
  - **Verified:** ruff clean, **mypy --strict clean (36 files)**, **30 pytest tests green** (mocked
    LLM/clients + MemorySaver), FastAPI app + `ash` CLI import/boot. Live Postgres/LLM runs pending
    real credentials + a `.env`. **Open follow-ups:** real Reviewer (maker/checker), bounded Fixer
    loop, worktree cleanup moves from Coding to Merge once Reviewer/Fixer are real, deferred
    post-comment, code-grounding depth.

- **2026-06-10 — Import package renamed `agent_system` → `ash`.** Completed the rename the earlier
  monorepo entry only did for the dist/script: moved `engine/src/agent_system/` → `engine/src/ash/`,
  updated the `pip` console-script (`ash = "ash.main:cli"`), ruff `known-first-party`, all external
  imports (tests, `config/settings/base.py`, `apps/house/.../build.py`), the CLI `prog` name, and all
  doc/comment references (README, CLAUDE.md, this plan). Internal imports were already relative, so
  no engine-internal edits beyond docstrings. Reinstalled editable; removed the stale `agent-system`
  console script. Verified: imports OK, `ash --help` OK, pytest green (6). Also **removed the Addy
  Osmani / "Loop Engineering" article attribution** from the plan (reworded to the generic
  "loop-engineering" pattern) per user request.

- **2026-06-10 — Plan created.** Analysis of `agent_architecture.md` vs. Loop Engineering; phased
  plan (0–5); locked decisions #1–#5; replicability model (§9).
- **2026-06-10 — Decisions extended.** Added #6 (pluggable triggers/sinks) and #7 (UI as a later,
  separate layer); generalized repo topology (§7a) to `fork` / `single` / `closed-source` modes;
  added §8 (Extensibility). Recorded the working agreement to keep this plan current.
- **2026-06-10 — Monorepo restructure + tooling (project = ASH).** Reorganized into a monorepo:
  engine → `engine/src/ash/`, Django app → `apps/house/` (label `house`), Django project →
  `config/` with split settings `settings/{base,dev,prod}.py`, docs+plan → `docs/`, added `tests/`.
  `config.py` now finds the repo root robustly (walks up for `projects/`/`pyproject.toml`, `ASH_ROOT`
  override) instead of hardcoding depth. Added **ruff** (lint+format, replacing flake8/isort/black,
  `UP042` ignored — str+Enum is intentional) and **pytest** (6 green tests). Added **Dockerfile**,
  **docker-compose.yml**, **.dockerignore**, **README.md**; expanded the `justfile` (lint/fmt/test/
  check/docker). DB renamed `runtime/ash.sqlite3`. Package/script renamed to `ash`. Engine stays
  Django-free. Verified: ruff clean, pytest green, `manage.py check` OK, CLI + config load OK.
  **Pending (handed to user):** physically rename the root folder to `ash` + rebuild `.venv` (breaks
  the editable install / venv paths if done mid-session).
- **2026-06-10 — Build team + Board + Django control plane.**
  - **Build-team flow** (replaces the spec-in-PR stub): added `connectors/board.py` (FileBoard sink —
    specs as `.md`+`.json`), `tools/code_intel.py` (read-only repo tree / ripgrep / file read,
    sandboxed), `agents/research_agent.py` (grounded `ImplementationPlan`), `agents/coding_agent.py`
    (`CodeChange` full-file edits applied in the worktree). Rewired `pipeline.py`: issue → spec →
    **Board** → worktree → research → **code** → commit → push → **PR carries code** → merge gate →
    worktree cleanup (`--keep` to retain). Verified live: PR #2 carried 4 code files; spec went to
    the board (separation confirmed).
  - **Known limitation (follow-up):** research/coding grounding is shallow — v1 fabricated internal
    paths (e.g. `apps/admin/*.jsx` + Redux) for a TS/MobX codebase. Next: verify paths against the
    real tree and read actual files before editing.
  - **Django control plane** (`make this a Django project`): engine is now an installable package
    (`pyproject.toml`, `pip install -e .[server]`); added Django project `controlplane/` + app
    `house/` with multi-tenant models **Client → Project → Run** (persisted `WorkflowState`), admin
    UI, and `manage.py build` bridging Django ↔ engine. Verified: migrations apply, `build` persists
    a Run (PR #3, full history). Decision #14. The engine stays Django-free; Django depends on it.
- **2026-06-10 — Vision elevated + flow corrected.** Reframed the project as a **multi-tenant
  agentic software house** (new §0: clients=humans, staff=agents, parallel engagements; decision
  #13). Codified the **spec→Board / code→PR separation** (decision #12, §4c, updated §8 sinks): the
  walking skeleton's spec-in-PR is a temporary plumbing artifact to be replaced once the Coding
  agent produces real code. Extended §9 to multi-tenancy (SaaS packaging = Phase 5+, must not alter
  the loop). Updated Phase 1 exit criteria accordingly.
- **2026-06-10 — Phase 1 walking skeleton GREEN (first live PR).** Ran `build` against the user's
  existing clone end-to-end: PM spec (`gpt-oss-120b`) → worktree off `origin/preview` → commit spec
  doc → push over HTTPS → **draft PR #1** on `zahid-arbisoft/plane`, parked at the human merge gate.
  Decisions added: #10 (git auth = HTTPS via `gh`; switched `git_repo.py` fetch/push to the HTTPS
  URL + `gh auth setup-git`, since the clone's origin is SSH and ssh-agent isn't available headless),
  #11 (model choices + LLM JSON-mode fallback after Groq's small-llama tool-calling validator
  rejected otherwise-valid output). Open follow-up: worktrees are left in place after a run (need a
  cleanup step / `--keep` flag).
- **2026-06-10 — Phase 1 walking skeleton built.** Scoping decided: *walking skeleton first*, against
  the user's *existing local clone*. Added: `state.py` (`WorkflowState` w/ stage/status/history/
  retry), `gates.py` (`ApprovalGate`), `connectors/git_repo.py` (worktree-per-ticket on an existing
  clone: ensure upstream, sync base, branch, commit, push), `connectors/pr.py` (fork-internal draft
  PR via authenticated `gh`), `pipeline.py` (issue→spec→worktree→**stub doc change**→commit→push→PR,
  merge `ApprovalGate`), CLI `build` + `just build`. Config gained `work.mode` and
  `work.local_repo_path` (+ `LOCAL_REPO_PATH` env override). The coding step is a STUB (writes the
  spec to `.agent-loop/issue-N.md`) until real build-team agents land. Verified imports/config/gate/
  state/CLI; **live run pending** the clone path + LLM creds.
- **2026-06-10 — Agent roster + RFC + self-instructions.** Split the monolithic Dev agent into a
  **build team** (Research/Spike, Dev/Coding, Documentation) — decision #8, §4a. Added an **optional
  RFC stage** between PM and build (decision #9, §4b, Phase 1.5). Added a per-project `pipeline:`
  concept for composing/ordering agents. Created **`CLAUDE.md`** (standing self-instructions:
  always update the plan + changelog, design rules, env notes) — to re-read at the start of sessions.
- **2026-06-10 — Phase 0 implemented.** Built the config-driven engine skeleton:
  - `src/ash/config.py` — env LLM settings + `projects/<name>.yaml` loader (Pydantic).
  - `src/ash/llm.py` — provider-agnostic client (`anthropic` + `openai`-compatible for
    local/LiteLLM gateways), structured output via tool calling, returns token usage.
  - `src/ash/schemas.py` — `Spec` (epic/technical_spec/tickets/risk_assessment).
  - `src/ash/connectors/github.py` — read-only issue fetch/list (requests).
  - `src/ash/agents/pm_agent.py` — issue → structured spec.
  - `src/ash/main.py` — CLI (`list`, `spec`); `justfile` with `setup/list/spec/doctor/clean`.
  - `projects/plane.yaml` (base branch `preview`), `skills/plane/SKILL.md`.
  - Smoke-tested: config load, live issue listing, schema round-trip, no-key guard. **LLM call not
    yet validated against a strong model** (pending spec-quality check).
  - Note: user's `.env` points at a LiteLLM gateway (`/v1`) → must use `LLM_PROVIDER=openai`.
