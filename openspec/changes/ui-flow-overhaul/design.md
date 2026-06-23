# Design: UI & Flow Overhaul

## Context

Existing backend already provides most control primitives (see exploration):
`Runner.{start_run, resume_run, retry_run, stop_run, resume_stopped, regenerate_spec,
refine_ticket, refine_story_code, build_from_spec}`, `BaseAgent._trigger_gate()`
(interrupt-based manual triggering), and `AgentLLMExchange` with unused `context`/`code`
columns. Manual is already the per-agent default except PM. This design maximizes reuse and
concentrates new work in the web layer plus four small backend gaps.

## Decisions

### D1 — Run cockpit = pipeline rail + stage panel (one page per run)
A single template `run_cockpit.html` at `/ui/run/{run_id}` and `/ui/run/{run_id}/{stage}`.

- **Pipeline rail** (top, sticky): ordered stage chips `intake → pm → rfc → research → dev
  → reviewer → fixer`. Each chip shows a status dot and a token chip. Build stages
  (research/dev/reviewer/fixer) are **per-story**; when a run has >1 story the rail shows a
  story selector and the build chips reflect the selected story.
- **Stage panel** (below): the selected stage's workbench body. Reuses/refactors the
  existing `_pm_workbench_body.html` and `_dev_story_card.html` bodies into per-stage
  partials `_stage_<name>.html` sharing a common controls macro.
- **Live updates**: one SSE stream `/ui/run/{run_id}/events` re-renders the rail + active
  panel every ~1.5s until terminal/awaiting (same pattern as today, consolidated).
- Deep-linking a stage selects it server-side (so it works without JS); Alpine handles
  client-side switching without a round-trip when already loaded.

Rationale: reuses existing SSE + workbench bodies; gives the n8n-ish flow feel; "separate
page per agent linked by run id" is realized as `/ui/run/{id}/{stage}` deep links.

### D2 — Fully-manual default
`DEFAULT_AUTO_TRIGGER_AGENTS` (config/settings.py) changes from `frozenset({"pm"})` to
`frozenset()`. Every gateable agent now interrupts via `_trigger_gate()` on entry unless a
DB/YAML policy sets `trigger=auto`.

- **Intake stays auto.** Intake only fetches the issue (no LLM cost, no client decision);
  making it manual adds friction with no benefit. Intake does not call `_trigger_gate()`
  today and won't. Documented as the one always-auto step.
- **PM becomes manual.** `PMAgent.run` must call `_trigger_gate("pm")` at entry (verify;
  add if missing). A fresh run therefore pauses at `pm` with `status=awaiting_trigger,
  pending_trigger="pm"`. The cockpit shows PM "awaiting trigger" with a **Trigger** button.
- The existing `manual_trigger` interrupt + `Runner.resume_run(..., "run")` path already
  drives this; no graph topology change.

Risk: tests that assume PM auto-runs (e.g. `test_runner.py` start_run→completed). These
will be updated to trigger PM (or set policy auto in the test fixture).

### D3 — Custom prompts
Add to `WorkflowState`:
```python
custom_prompts: dict[str, str] = Field(default_factory=dict)  # agent_name -> instructions
run_prompt: str = ""  # global free-text instruction set at run start
```
- **At run start**: the new-run form's custom-prompt field sets `run_prompt`; it is appended
  to PM's (or the first build agent's, for raw_to_dev) user prompt.
- **At re-trigger**: a new `Runner.retrigger_agent(run_id, agent, *, ticket_id=None,
  custom_prompt="")` seeds `custom_prompts[agent]` (and per-story feedback when ticket_id is
  given), resets that agent's sub-state, and re-enters via `aupdate_state(as_node=...)`.
- Agents fold `custom_prompts.get(self.name)` into their prompt for that pass via a shared
  `BaseAgent` helper, then it is cleared on return (consume-once, mirroring `feedback`).

### D4 — Generalized per-agent HITL feedback
Today only `PMState.feedback`/`ticket_feedback` and `CodingState.feedback` exist. Add a
nullable `feedback: str | None = None` to `ResearchState`, `ReviewerState`, `FixerState`,
`RFCState`. A single generalized runner method handles all:
```python
async def refine_agent(run_id, *, agent, ticket_id=None, feedback="", custom_prompt="", wait=False)
```
- For run-level agents (pm/rfc): fork `as_node=<predecessor>` with a fresh sub-state carrying
  feedback+custom_prompt.
- For per-story build agents (research/dev/reviewer/fixer): reset the story from that step
  onward (preserving `branch`/`pr_url`), seed feedback+custom_prompt, re-enter via
  `as_node="plan_stories"` — exactly like `refine_story_code` generalized.
- `refine_story_code` and `refine_ticket` become thin wrappers over this (or are kept for
  back-compat and delegate).

Each agent consumes `state.<ns>.feedback` into its prompt and returns `feedback: None`.

### D5 — Dev code-to-LLM capture
`BaseAgent._capture_exchange()` already accepts `context` and `code`. Wire the producers:

- **Dev (coding) & Fixer**: in `_code()`/fix loop, build a `code_context` string of the
  file slices actually sent to the model and pass `code=code_context` to `generate()`.
  Also pass the brief/spec as `context=`.
- **Research**: pass the grep/read results sent to the model as `code=`.
- **Token breakdown**: store nothing new — the I/O viewer computes/derives the split by
  showing `prompt_tokens` alongside the `code`/`context` byte/char sizes, and (best-effort)
  a tokenizer estimate per section. The headline number stays `prompt_tokens` from the API.

### D6 — Rename `coding` → `Dev`
- **Display/label**: everywhere user-facing says "Dev".
- **Agent name**: introduce `name = "dev"` as the canonical agent name. Keep the Python
  class importable; rename `CodingAgent`→`DevAgent` (alias `CodingAgent = DevAgent` for one
  release), node name `"coding"`→`"dev"` in the story subgraph, config key `agents.coding`→
  `agents.dev` (read both; prefer `dev`).
- **State**: `CodingState`→`DevState` with `CodingState` alias.
- **DB rows**: historical `AgentLLMExchange.agent_name="coding"` and
  `AgentTask.agent_name="coding"` are mapped to "dev" at **read time** via a small alias map
  in the query/display layer. No migration, no data rewrite.
- **Graph state field**: `WorkflowState.stories[*].coding` (StoryState.coding) — rename to
  `.dev` with a Pydantic alias so old checkpoints still deserialize.

Rationale: read-time alias avoids a risky bulk migration of checkpoint JSON + DB history
while presenting a consistent "Dev" name.

### D7 — New-run form: Source / Destination split
`run_new.html` becomes two sections (single page, not a multi-step wizard — fewer clicks):
- **Source**: project (req), issue connector, item id, attachments, **custom prompt**.
- **Destination & options** (collapsible "Advanced"): task sink, story mode, intake mode.
- Submit posts to the same `POST /ui/runs`; `run_prompt` added. Redirect → `/ui/run/{id}`
  (cockpit) for all runs (pm_only flag still controls graph routing, not the landing page).

### D8 — Dedicated I/O page
New `/ui/io` global view + keep cockpit-scoped I/O (`/ui/run/{id}/io`). One template
`io_log.html` parameterized by filters (run_id, agent, ticket, phase). Improvements:
- Filter bar (run / agent / story / phase) + free-text search.
- Per-exchange card: collapsible **Sent** (messages, with `context` and `code` shown as
  distinct labeled blocks), **Received** (content / tool_calls / parsed), and a **token
  strip** (prompt→completion, plus context/code char sizes).
- Token analytics header (totals by agent/phase) reusing `db/metrics`.

### D9 — Navigation / IA
New sidebar: **Dashboard · Runs (hub list) · Work board · Agents · Connectors · Approvals ·
I/O logs** + system (Admin, API docs). Removed: PM workbench, Dev workbench top-level
entries (now stages in the cockpit). `+ New` menu keeps New run / New connector.

## Routing changes

| Old | New |
|---|---|
| `GET /ui/runs` (table) | removed → redirect to `/ui/runs` (hub list, redesigned) |
| `GET /ui/runs/{id}` (timeline) | removed → redirect to `/ui/run/{id}` (cockpit) |
| `GET /ui/runs/{id}/events` | → `/ui/run/{id}/events` |
| `GET /ui/runs/{id}/llm`, `/ui/dev/{id}/llm` | → `/ui/run/{id}/io` (+ global `/ui/io`) |
| `GET /ui/pm/{id}`, `/ui/dev/{id}` | → `/ui/run/{id}/pm`, `/ui/run/{id}/dev` |
| `GET /ui/pm-workbench`, `/ui/dev-workbench` | removed → `/ui/runs` (hub list) |
| `POST /ui/{pm,dev,runs}/{id}/...` actions | consolidated under `POST /ui/run/{id}/...` |

Old paths return 308 redirects for one release where a sensible target exists.

## Risks / tradeoffs

- **Large template churn.** Mitigated by extracting shared macros (controls, status dot,
  token chip, story card) into `_macros.html` so each stage panel is thin.
- **Fully-manual PM breaks existing tests + changes default UX.** Acknowledged and chosen;
  tests updated; auto remains one policy toggle away.
- **Rename via read-alias** keeps history readable but means two names coexist in code for a
  release; clearly documented in the plan changelog.
- **Pipeline rail per-story complexity.** Build stages are per-story; the rail shows a story
  switcher rather than trying to render every story's pipeline at once.

## Migration / rollout

Phased (see tasks.md): backend foundations → cockpit → form + dashboard + hub list →
I/O page → polish. Each phase keeps the app runnable; old routes redirect until the cockpit
lands, then are removed.
