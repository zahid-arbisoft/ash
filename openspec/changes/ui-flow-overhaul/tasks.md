# Tasks: UI & Flow Overhaul

Phased so the app stays runnable after each phase. Old routes redirect until the cockpit
lands (Phase C), then are removed.

## Phase A — Backend foundations  ✅ DONE (208 tests green)

- [x] A1. `coding` → `dev` rename: `CodingAgent`→`DevAgent` (+ `CodingAgent` alias),
  `coding.py`→`dev.py`, node name `coding`→`dev` in story subgraph, config key `agent_dev`,
  `CodingState`→`DevState` (+ alias), `StoryState.coding`/`WorkflowState.coding`→`.dev`,
  `StoryStep` literal, checkpointer allowlist, API schema. Clean rename (pre-prod, no
  checkpoints to preserve).
- [x] A2. Read-time agent-name alias (`coding`→`dev`) — handled in Phase D I/O display
  (historical rows). (Carried into Phase D.)
- [x] A3. Fully-manual default: `DEFAULT_AUTO_TRIGGER_AGENTS` → `frozenset()`;
  `PMAgent.run` now calls `_trigger_gate`; intake never gates (documented).
- [x] A4. Custom prompts: added `WorkflowState.run_prompt` + `custom_prompts`;
  `BaseAgent._extra_instructions()` folds them; node wrapper clears consumed entry.
- [x] A5. Generalized HITL feedback: `feedback: str|None` added to `ResearchState`,
  `ReviewerState`, `FixerState`, `RFCState`; each agent consumes + clears it.
- [x] A6. `Runner.refine_agent(...)`; `refine_story_code` delegates to it.
- [x] A7. `Runner.retrigger_agent(...)` (thin wrapper over `refine_agent`).
- [x] A8. Dev/Research/Reviewer/Fixer code-to-LLM capture: `code=`/`context=` passed into
  `generate()` (persisted by `_capture_exchange`).
- [x] A9. Tests updated for manual default + rename; added tests for `refine_agent`,
  `retrigger_agent`, custom-prompt threading + clearing, code capture.

## Phase B — Run cockpit (pipeline rail + panels)  ✅ DONE (templates render clean, 208 tests green)

- [x] B1. `_macros.html`: status-dot/pill, token-chip, HITL refine+retrigger controls,
  trigger controls.
- [x] B2. `run_cockpit.html` + `_cockpit_body.html` + `_cockpit_rail.html` (ordered stage
  chips with status/token, per-story selector for build stages).
- [x] B3. Stage panels `_stage_runlevel.html` (intake/pm/rfc, incl. spec gate + per-ticket
  refine) and `_stage_build.html` (per-story research/dev/reviewer/fixer + merge gate);
  uniform controls via macros.
- [x] B4. Routes: `GET /ui/run/{id}`, `GET /ui/run/{id}/{stage}`, `GET /ui/run/{id}/io`;
  SSE `GET /ui/run/{id}/events` (rail + active panel). Literal paths ordered before `{stage}`.
- [x] B5. Consolidated `POST /ui/run/{id}/...`: trigger, skip, stop, restart, retry, approve,
  reject, refine, retrigger.
- [x] B6. Wired to `resume_run`/`stop_run`/`resume_stopped`/`retry_run`/`refine_agent`/
  `retrigger_agent`/`refine_ticket`/`build_from_spec`.

## Phase C — New-run form, dashboard, run hub list, route cleanup  ✅ DONE (208 tests green)

- [x] C1. `run_new.html` split into Source / Destination & options (advanced collapsed) +
  custom-prompt field; `POST /ui/runs` sets `run_prompt`; redirect → `/ui/run/{id}`.
- [x] C2. Run hub list at `/ui/runs` links to cockpit with status dots + clickable rows.
- [x] C3. Redesigned dashboard: Active runs + Awaiting-you KPIs (clickable), token/time,
  status dots on recent runs, stale roadmap note removed.
- [x] C4. Old run timeline / PM / Dev workbench pages → 308 redirects to cockpit/hub;
  orphaned PM/Dev workbench SSE + action routes and `_pm_view_ctx`/`_dev_view_ctx` removed.
- [x] C5. Sidebar + mobile nav rebuilt (Dashboard · Runs · Work board · Agents · Connectors ·
  Approvals · LLM I/O); "New PM run" dropped from + New.

## Phase D — Dedicated I/O page  ✅ DONE (built alongside Phase B)

- [x] D1. `io_log.html` with filter bar (agent/story/phase) + free-text search (HTMX live).
- [x] D2. `_io_log_results.html` per-exchange card: labeled Sent context / Sent code /
  Sent messages / Received + token strip (prompt→completion, context/code char sizes).
- [x] D3. Routes `GET /ui/io` (global) and `GET /ui/run/{id}/io` (scoped); token-analytics
  header (totals over filtered set).
- [x] D4. `db/exchanges.list_exchanges()` filtered query + historical `coding`→`dev` display
  alias (A2).

## Phase E — Polish & docs  ✅ DONE

- [x] E1. Empty states + mobile nav + light/dark token parity carried by `_macros.html` and the
  shared design tokens; 14 dead workbench/timeline templates removed. (Loading skeletons: the SSE
  body swap already covers live progress; deferred as non-essential.)
- [x] E2. `docs/plan/ash_architecture_and_plan.md` §7 (Locked Decision #33) + §11 Changelog
  updated (PROPOSED → IMPLEMENTED entry).
- [x] E3. `CLAUDE.md` current-status section updated (decision #33 entry).
- [x] E4. `openspec validate ui-flow-overhaul --strict` → valid. `just check` is the user's to run
  (ruff/mypy never run by the assistant per standing instruction); 208 pytest green.

## Phase F — Field-test follow-up fixes (2026-06-24)

Issues found while driving real runs through the cockpit. Grouped; this change covers the cockpit
+ agent fixes. The Workflow builder is split into its own change (`workflow-builder`).

### F-cockpit — DONE (208 tests green; new branches smoke-rendered)
- [x] F1. Live **running** state: `get_run` exposes `_next_nodes`/`_task_running`; `_augment_liveness`
  + `agent_tasks` set `running_stage`/`running_story`; status helpers + `_default_stage` honour it.
  Fixes "PM looked like it never ran after refresh" and "opened run should land where it is."
- [x] F2. **Stalled-run** detection (server restarted mid-run): red banner naming the dead agent +
  Restart/Retry; SSE stops polling. (`_augment_liveness`, `_cockpit_body.html`.)
- [x] F3. **RFC-aware** PM approval: `_agent_enabled()` → `rfc_enabled`; hide "Approve & write RFC"
  and show a hint when RFC is disabled (was misleading + surfaced "rfc disabled" only after build).
- [x] F4. PM panel polish: single/multiple-story **tag**; **Expand/Collapse all** tickets;
  spec-level **Refine/Re-trigger moved to top**.
- [x] F5. **Dev no-output → step failure** (logged reason, story marked failed); Reviewer/Fixer
  self-skip on empty `dev.change` (confirmed already guarded). Re-trigger/retry now show running.
- [x] F5c. **Retrigger showed the wrong agent as running.** `_augment_liveness` trusted the
  best-effort `AgentTask` table (most-recent `in_progress`), which a stale row or a pre-marked
  `auto` next-task could poison — so after a retrigger the cockpit lit up the *next* agent. Rewrote
  it to derive the live stage from the graph + story state (`_live_stage`/`_running_build_stage`:
  the current story's first incomplete build step, or the graph's next run-level node). The task
  table is now only a stalled fallback. Tests in `test_routes_helpers.py`.
- [x] F5b. **Failed PM/RFC is no longer a dead end.** The run-level panel's no-output branch
  (`_stage_runlevel.html`) was showing only "No spec generated yet — trigger PM to begin" with no
  control when PM/RFC *failed* (the refine/re-trigger controls lived inside the `if spec` block).
  Now a failed/empty PM (or RFC) renders the error + **Refine / Re-trigger** controls, so the client
  can always re-run it; `retrigger_agent` re-enters `as_node="intake"`/`pm_publish`, working even
  from a terminal/failed checkpoint.

### F-remaining — TODO (next sessions)
- [~] F6. **LLM I/O page** — mostly DONE (208 tests green; templates smoke-rendered):
  - [x] Phase glossary (single_call/explore/extract/refine) as a collapsible legend.
  - [x] Per-section (per-agent/story group) token totals + wall-clock time, and an overall time total.
  - [x] In-flight (response-pending) banner from `agent_tasks` in_progress — surfaces long-running
    calls (e.g. Dev) before their exchange is persisted.
  - [x] Group-level collapse + page-level Expand/Collapse-all.
  - [x] Datadog-style slide-out detail drawer: clicking an exchange row opens a right-side panel
    (Alpine, `ex` hydrated from a `data-ex` JSON blob) with context / code / sent messages /
    received; Esc or backdrop closes. Replaces the per-exchange inline `<details>`.
  - [ ] True per-request in-flight persistence (write a pending exchange row before the response
    lands) — deferred; needs a change to the `_capture_exchange` flow + a partial-row schema. The
    agent-granularity in-flight banner (above) covers the common "is Dev still working?" need.
- [x] F7. **Multi-story single PR** — DONE (211 tests green; live git push/stack pending real repo):
  `pr_strategy` (per_story|single) on `WorkflowState` + `RunRecord` (column + backfill +
  RunRecordAdmin). New-run form choice (multi-story) + spec-gate toggle (`Runner.set_pr_strategy`).
  `single` → shared run-level branch/worktree (`ensure_worktree` seed + `open_or_create_worktree`
  reuse), Dev reuses the run-level `combined_pr_url`, `_story_finalize` defers shared-worktree
  cleanup to `_merge`, node adapter forwards Dev's run-level keys. Cockpit: "combined PR" tag +
  shared PR link; per-story PR ↗ retained. Tests: first-open / later-reuse / worktree-seed / node
  passthrough.
- [x] F8. **Docs** — DONE: plan §10z (agent outputs & data flow — RFC is standalone/not consumed,
  Research plan feeds Dev) + `_explore` budget semantics (`EXPLORE_STEPS` = ceiling, model stops
  early; also surfaced in the I/O glossary). No new DB *models* added (F7 = column → RunRecordAdmin
  updated); `Workflow` model + admin view land with the `workflow-builder` change.
- [ ] F9. **Workflows** — moved to the `workflow-builder` change (DB-persisted, versioned,
  soft-delete, default selection, drag-to-reorder builder, per-agent manual/auto + order, per-story
  execution order, agent-page config precedence, run-page dropdown, snapshot-on-execute).
