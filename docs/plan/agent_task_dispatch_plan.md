# Plan — Per-Agent Task Pages & Dispatch System

> **Status:** PLANNING (2026-06-16)
> **Goal:** Give every agent its own task-queue page (like PM runs), DB-backed configurable
> dispatch policies (concurrency, quota, retries), a background dispatcher that auto-picks tasks,
> and a config UI that lets the operator adjust policies live without editing YAML.
>
> **Depends on:** current pipeline (Intake → PM → RFC → Research → Coding → Reviewer → Fixer →
> Merge), `RunRecord` / `SpecRecord` in DB, LangGraph `_trigger_gate()` mechanism.

---

## 0. Problem statement

Today all agents run as **steps inside a single LangGraph run**. There is no per-agent task view:
- You can't see "which issues are waiting for Research to pick up".
- You can't configure "Research should only work on 2 tasks at once, retry up to 3 times".
- The only HITL gate with a UI is the PM spec-review gate; trigger-gate interrupts for other agents
  have backend code but **no UI surface** to trigger them or see what's waiting.

We want to add a **dispatch layer** on top of the existing pipeline: each agent has its own queue
of tasks it needs to process, a configurable policy that governs auto-pickup, and a dedicated UI
page.

---

## 1. Core concepts

### 1.1 AgentTask — the per-agent unit of work

An `AgentTask` is a record that tracks **one agent's work on one run**. It is a projection of
pipeline state onto a per-agent view. Every time the pipeline reaches an agent node, an
`AgentTask` row is created (or updated) for that run × agent pair.

```
AgentTask lifecycle:
  pending ──► in_progress ──► completed
      │                           │
      └── (retry < max_retries) ◄─┘ (on failure)
              │
              └──► failed  (max_retries exceeded)
              └──► cancelled (run rejected / abandoned)
```

States:
| State | Meaning |
|---|---|
| `pending` | Work is ready; waiting for the dispatcher to assign or human to trigger |
| `scheduled` | Dispatcher has scheduled it to run at `scheduled_at` (future time window) |
| `in_progress` | The agent is actively running for this task |
| `completed` | Agent finished successfully |
| `failed` | Agent failed; retries exhausted |
| `cancelled` | Run was rejected, or the task was manually cancelled |

### 1.2 AgentPolicy — extended with dispatch limits

Extends the existing `AgentPolicy` (trigger / enabled) with:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `trigger` | `auto\|manual` | `auto` | Auto-pickup or wait for human trigger |
| `enabled` | `bool` | `true` | Agent is active in the pipeline |
| `concurrency_limit` | `int` | `1` | Max simultaneous `in_progress` tasks |
| `daily_quota` | `int \| None` | `None` | Max tasks completed per calendar day (None = ∞) |
| `max_retries` | `int` | `0` | Times to retry a failed task before marking `failed` |
| `schedule_cron` | `str \| None` | `None` | Cron expression — run only during these windows (None = always) |

### 1.3 AgentPolicyRecord — DB-backed policy overrides

The YAML config (`projects/<name>.yaml`, `agents:` map) is the baseline. The operator can
override any field via the UI, stored in a `agent_policy_records` table. Resolution order:

```
DB override (AgentPolicyRecord)  >  YAML AgentPolicy  >  code default
```

Reason: YAML is version-controlled and safe; DB overrides allow live tuning without a deploy.

### 1.4 Dispatcher — background auto-pickup

A background `asyncio` loop (started in FastAPI lifespan) that ticks every N seconds
(default 30s, configurable via `DISPATCH_INTERVAL_SECONDS`). For each agent × project:

1. If `trigger != "auto"` or `enabled == False` → skip.
2. Count `in_progress` tasks → if ≥ `concurrency_limit` → skip.
3. Count today's `completed` → if ≥ `daily_quota` → skip.
4. Pick the oldest `pending` task(s) up to the available concurrency slot(s).
5. Mark them `in_progress`; call `runner.resume_run(run_id, "run")` to activate the agent.

On agent failure: if `retry_count < max_retries`, reset to `pending`, increment `retry_count`.
Otherwise mark `failed`.

---

## 2. Data model changes

### 2.1 New table: `agent_tasks`

```python
class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: int                         # PK
    agent_name: str                 # pm | research | coding | reviewer | fixer | rfc
    project: str                    # project name (matches RunRecord.project)
    run_id: str                     # FK → run_records.run_id
    item_id: str                    # issue / ticket identifier (display)
    title: str                      # human-readable task title (from spec epic title)
    status: str                     # pending | scheduled | in_progress | completed | failed | cancelled
    retry_count: int   = 0
    max_retries: int   = 0          # snapshot of policy at task creation time
    scheduled_at: datetime | None   # None = run ASAP; set for cron-windowed agents
    started_at:   datetime | None
    completed_at: datetime | None
    result_ref:   str  | None       # PR url, doc path, ticket ref, etc.
    error:        str  | None       # last error message (overwritten on each attempt)
    created_at:   datetime
    updated_at:   datetime
```

Indexes:
- `(agent_name, project, status)` — for dispatcher queries
- `(run_id, agent_name)` — for run-detail lookups
- `(agent_name, project, created_at)` — for daily-quota count

### 2.2 New table: `agent_policy_records`

```python
class AgentPolicyRecord(Base):
    __tablename__ = "agent_policy_records"

    id: int
    project: str
    agent_name: str
    trigger: str            = "auto"
    enabled: bool           = True
    concurrency_limit: int  = 1
    daily_quota: int | None = None
    max_retries: int        = 0
    schedule_cron: str | None = None    # e.g. "0 9-18 * * 1-5" (weekday business hours)
    updated_at: datetime

    # unique constraint: (project, agent_name) — one policy row per agent per project
```

### 2.3 Extend `AgentPolicy` (Pydantic model in `config/settings.py`)

```python
class AgentPolicy(BaseModel):
    trigger: TriggerMode       = "auto"
    enabled: bool              = True
    concurrency_limit: int     = 1
    daily_quota: int | None    = None
    max_retries: int           = 0
    schedule_cron: str | None  = None
```

Updated `projects/<name>.yaml` schema example:
```yaml
agents:
  research:
    trigger: auto
    concurrency_limit: 2
    daily_quota: 10
    max_retries: 2
  coding:
    trigger: manual        # human must click "Trigger" for each task
    concurrency_limit: 1
    max_retries: 1
  reviewer:
    trigger: auto
    concurrency_limit: 3
```

---

## 3. Task creation in the pipeline

Each graph node creates / updates its `AgentTask` as a **side effect** (best-effort, never
blocking the graph). A new helper `db/tasks.py` provides the CRUD operations.

### Where tasks are created

| Graph transition | Task created for | Task marked completed for |
|---|---|---|
| `pm_publish` approves spec | `research` (1 task per run) | `pm` |
| `research` node ends | `coding` | `research` |
| `coding` node ends (PR opened) | `reviewer` | `coding` |
| `reviewer` node — `changes_requested` | `fixer` | — |
| `reviewer` node — `approve` + merge | — | `reviewer` |
| `fixer` node ends | `reviewer` (loop back) | `fixer` |
| `merge` node ends | — | `reviewer` (if not already) |

PM tasks are created at run start (from `IntakeAgent`); initial status `in_progress` (PM always
runs automatically in the current pipeline, no trigger gate).

### Task title sourcing

- **PM task**: `item_id` from run, title = `"Process issue {item_id}"`
- **Research task**: title from `spec.epic.title` (available after PM)
- **Coding task**: title from `spec.epic.title`
- **Reviewer / Fixer**: title = `"Review PR for {epic_title}"` / `"Fix review: {epic_title}"`

---

## 4. Trigger gate integration

`BaseAgent._trigger_gate()` currently calls `interrupt()` and returns the decision. Extend it to:

1. **Before interrupt**: create / find the `AgentTask` for this run × agent, set status =
   `pending`.
2. **After resume (decision == "run")**: update status → `in_progress`, set `started_at`.
3. **After agent completes** (in `make_node` wrapper in `nodes.py`): update status →
   `completed`, set `completed_at`, `result_ref`.
4. **On error** (existing `error` capture in `nodes.py`): update status → `failed` or
   back to `pending` if `retry_count < max_retries`.

This keeps the graph node wrapper as the lifecycle owner — no change to `BaseAgent.generate()`.

---

## 5. Dispatcher

New module: `src/ash/graph/dispatcher.py`

```python
class DispatchService:
    """Background ticker that auto-triggers pending AgentTask rows per policy."""

    def __init__(self, runner: Runner, interval_seconds: int = 30): ...

    async def tick(self, db: AsyncSession) -> None:
        """Called every interval. Picks up eligible pending tasks."""
        for project in await list_active_projects(db):
            policy_resolver = PolicyResolver(project, db)
            for agent_name in DISPATCHABLE_AGENTS:   # research, coding, reviewer, fixer
                policy = await policy_resolver.resolve(agent_name)
                if not policy.enabled or policy.trigger != "auto":
                    continue
                active = await active_task_count(db, agent_name, project)
                if active >= policy.concurrency_limit:
                    continue
                if policy.daily_quota:
                    done_today = await today_completed_count(db, agent_name, project)
                    if done_today >= policy.daily_quota:
                        continue
                slots = policy.concurrency_limit - active
                tasks = await get_pending_tasks(db, agent_name, project, limit=slots)
                for task in tasks:
                    await self._dispatch(task, policy, db)

    async def _dispatch(self, task: AgentTask, policy: AgentPolicy, db: AsyncSession) -> None:
        await update_task_status(db, task.id, "in_progress", started_at=utcnow())
        try:
            await self.runner.resume_run(task.run_id, "run")
        except Exception as exc:
            task.retry_count += 1
            if task.retry_count >= policy.max_retries + 1:
                await update_task_status(db, task.id, "failed", error=str(exc))
            else:
                await update_task_status(db, task.id, "pending", error=str(exc))

    async def run(self) -> None:
        """Long-running coroutine, started in FastAPI lifespan."""
        while True:
            async with get_async_session() as db:
                await self.tick(db)
            await asyncio.sleep(self.interval_seconds)
```

`PolicyResolver.resolve(agent_name)` merges DB override → YAML → code default (§1.3).

---

## 6. Runner interrupt type disambiguation (prerequisite)

`runner.get_run()` currently sets `pending_review=True` for ALL interrupts. Extend to:

```python
if snapshot.interrupts:
    payload = snapshot.interrupts[0].value
    if payload == "spec_review" or (isinstance(payload, dict) and payload.get("reason") == "spec_review"):
        state["status"] = "awaiting_review"
        state["pending_review"] = True
    elif isinstance(payload, dict) and payload.get("reason") == "manual_trigger":
        state["status"] = "awaiting_trigger"
        state["pending_trigger"] = payload.get("agent")   # e.g. "research"
    elif isinstance(payload, dict) and payload.get("reason") == "merge_approval":
        state["status"] = "awaiting_merge"
        state["pending_merge"] = True
```

And update `_run_timeline.html` with two new gate blocks:
- **Trigger gate** block: shown when `state.pending_trigger` — "Research is ready to start.
  Click Trigger to run it now." → `POST .../trigger`
- **Merge approval** block: shown when `state.pending_merge` — "Reviewer approved; ready to
  merge." → `POST .../approve`

---

## 7. UI — per-agent pages

### 7.1 Information architecture

`/ui/agents` (existing overview) stays but each card now links to:
`/ui/agents/{agent_name}` — a dedicated detail page.

### 7.2 Per-agent detail page layout

```
┌──────────────────────────────────────────────────────┐
│  [← Agents]  Research Agent                          │
│  "Investigates spikes; produces implementation plans" │
├──────────────────────────────────────────────────────┤
│  Configuration panel (editable)                       │
│  ┌────────────┐ ┌──────────────┐ ┌─────────────────┐ │
│  │ Trigger    │ │ Concurrency  │ │  Daily quota    │ │
│  │ [auto ▼]  │ │  limit [2]   │ │  [10 / day]     │ │
│  └────────────┘ └──────────────┘ └─────────────────┘ │
│  ┌────────────┐ ┌──────────────┐ ┌─────────────────┐ │
│  │ Max retries│ │ Cron window  │ │  Enabled [✓]    │ │
│  │    [2]     │ │  [9-18 M-F]  │ │                 │ │
│  └────────────┘ └──────────────┘ └─────────────────┘ │
│  [Save configuration]  [Reset to YAML defaults]       │
├──────────────────────────────────────────────────────┤
│  Stats bar                                            │
│  Active: 1  │  Today: 4  │  Success rate: 92%        │
├──────────────────────────────────────────────────────┤
│  Tasks                              [Filter ▼]        │
│                                                       │
│  ● Pending (3)                                        │
│  ┌──────────────────────────────────────────────────┐ │
│  │ plane#1234  "Add OAuth provider"    2h ago  [↺0] │ │
│  │ [Trigger →]                                      │ │
│  ├──────────────────────────────────────────────────┤ │
│  │ plane#1298  "Fix memory leak in…"   4h ago  [↺1] │ │
│  │ [Trigger →]                                      │ │
│  └──────────────────────────────────────────────────┘ │
│                                                       │
│  ● In Progress (1)                                    │
│  ┌──────────────────────────────────────────────────┐ │
│  │ plane#1201  "Refactor billing mod…" running ●    │ │
│  │ [View run →]                                     │ │
│  └──────────────────────────────────────────────────┘ │
│                                                       │
│  ● Completed (12)  Failed (1)  [Show →]               │
└──────────────────────────────────────────────────────┘
```

### 7.3 Routes to add

| Method | Path | Description |
|---|---|---|
| `GET` | `/ui/agents/{name}` | Per-agent detail page (tasks + config) |
| `POST` | `/ui/agents/{name}/config` | Save policy override to `AgentPolicyRecord` |
| `POST` | `/ui/agents/{name}/config/reset` | Delete DB override (revert to YAML) |
| `POST` | `/ui/tasks/{task_id}/trigger` | Manually trigger a `pending` task |
| `POST` | `/ui/tasks/{task_id}/cancel` | Cancel a `pending` or `in_progress` task |
| `GET` | `/ui/agents/{name}/tasks` (HTMX) | Task list partial for live reload |

### 7.4 Updated existing pages

- **`/ui/agents`** overview: each card gains `N pending` / `N in-progress` live count badge,
  links to detail page instead of generic "Start a run →"
- **`_run_timeline.html`**: add trigger-gate + merge-gate blocks (§6)
- **`/ui/approvals`**: show interrupt type label ("spec review" / "trigger gate: research" /
  "merge approval") so the operator knows why a run is paused

---

## 8. New `db/tasks.py` — CRUD helpers

```python
# Task management
create_agent_task(session, agent_name, run_id, project, item_id, title, max_retries) → AgentTask
upsert_agent_task(session, ...)                          # idempotent create-or-update
update_task_status(session, task_id, status, **fields)  → AgentTask
get_task_for_run(session, run_id, agent_name)           → AgentTask | None
list_tasks(session, agent_name, project, status=None, limit=50, offset=0) → list[AgentTask]
get_pending_tasks(session, agent_name, project, limit)  → list[AgentTask]
active_task_count(session, agent_name, project)         → int
today_completed_count(session, agent_name, project)     → int
task_stats(session, agent_name, project)                → TaskStats

# Policy management
get_policy_override(session, project, agent_name)       → AgentPolicyRecord | None
upsert_policy_override(session, project, agent_name, **fields) → AgentPolicyRecord
delete_policy_override(session, project, agent_name)    → None
resolve_policy(session, project_config, agent_name)     → AgentPolicy   # DB > YAML > default
```

---

## 9. Build order (5 phases, each keeps tests green)

### Phase D1 — Data model (DB tables + migration backfill)
- Add `AgentTask` + `AgentPolicyRecord` to `db/models.py`
- Add `ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` backfills to `db/base.py`
  `_PG_COLUMN_BACKFILLS` (same pattern already used)
- Extend `AgentPolicy` Pydantic model with new fields
- New `db/tasks.py` (CRUD helpers, no logic)
- Tests: model round-trip, task CRUD, policy resolution order

### Phase D2 — Runner interrupt disambiguation
- `runner.get_run()` — parse interrupt payloads, expose `pending_trigger` / `pending_merge`
- `_run_timeline.html` — add trigger-gate and merge-gate blocks
- `approvals.html` — label by interrupt type
- Tests: runner returns correct keys for each interrupt type

### Phase D3 — Task lifecycle in pipeline
- `graph/nodes.py` `make_node` wrapper — create/update `AgentTask` on entry/exit/error
- `IntakeAgent` — create PM task at run start
- `pm_publish` — on approval, create Research task; mark PM task completed
- Research/Coding/Reviewer/Fixer nodes — create next-stage task on success
- Tests: end-to-end task creation across the pipeline (mock LangGraph)

### Phase D4 — Dispatcher
- `graph/dispatcher.py` — `DispatchService` with `tick()` + `_dispatch()`
- Wire into FastAPI lifespan as a background asyncio task
- Config: `DISPATCH_INTERVAL_SECONDS` env var (default 30)
- Tests: dispatcher respects concurrency_limit / daily_quota / max_retries; retry logic

### Phase UI1 — Per-agent pages
- `web/routes.py` — add `GET /ui/agents/{name}`, `POST /ui/agents/{name}/config`,
  `POST /ui/agents/{name}/config/reset`, `POST /ui/tasks/{id}/trigger`,
  `POST /ui/tasks/{id}/cancel`
- New template `agent_detail.html` — config panel + task board + stats bar
- Update `agents.html` cards with live count badges + detail page links
- Update `approvals.html` with interrupt type labels
- Tests: all new routes return 200; task trigger calls `resume_run`

---

## 10. Open questions (to resolve before Phase D3)

| # | Question | Options |
|---|---|---|
| Q1 | Should one run have **one Research task** (for the whole spec) or **one per ticket**? | One per spec (current model, simpler) vs one per ticket (more granular, bigger refactor) |
| Q2 | Should `AgentTask` be created eagerly (at run start for the full pipeline) or lazily (when each stage is ready)? | Lazy (matches current pipeline flow — each stage spawns the next) |
| Q3 | `schedule_cron` field: how is timezone handled? | Store UTC offset in the policy, or always UTC and document it |
| Q4 | When `trigger=manual` and dispatcher is running, should dispatcher still show the task as `pending` without auto-resuming? | Yes — `manual` always requires human, dispatcher only acts on `auto` agents |
| Q5 | PM always runs automatically (no trigger gate). Should PM tasks be created retroactively from existing `RunRecord` rows? | Yes, on first migration tick: seed `AgentTask` from existing `RunRecord.status` |

---

## 11. What does NOT change

- The LangGraph `StateGraph` topology — same pipeline, same nodes, same edges
- `WorkflowState` namespaced sub-states — `AgentTask` is a DB projection, not the state of record
- The checkpointer — still the source of truth for live run state
- The existing HITL gates (spec_review, merge_approval) — only gaining UI for trigger_gate
- YAML config — still valid baseline; DB override is additive, not replacing
