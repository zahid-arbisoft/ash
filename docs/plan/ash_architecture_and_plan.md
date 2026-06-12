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
