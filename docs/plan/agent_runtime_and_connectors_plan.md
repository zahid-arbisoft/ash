# Plan — Agent runtime (create_agent + middleware) & MCP connectors

> **Status:** IN PROGRESS — **P0 + P1 done, P3 MCP loader done (hosted HTTP), P4 mechanism done**
> (2026-06-12). **Full team spec, connector overhaul, and Jira-style UI plan added 2026-06-15**
> (§10–§13). Active roadmap is **§13** (UI foundation first → agents → connectors, interleaved);
> §5's P2–P6 are folded into it and kept as the historical shipped-record. See §13 for next steps.
> **Goal:** stop reinventing the wheel — adopt the latest LangChain/LangGraph ecosystem at the
> agent layer, and replace bespoke issue integrations with **MCP connectors**.
> **Scope of this doc:** the agent inner-loop runtime, human-in-the-loop, and the connector layer.
> The macro orchestration graph (`graph/builder.py`), checkpointer, FastAPI, DB, admin, and UI stay.

---

## 0. Principles

1. **Prefer maintained OSS primitives** over hand-rolled code (the inner tool loop, approval gates,
   issue clients).
2. **Keep the deterministic SDLC orchestration** (LangGraph `StateGraph`) — only the *inside* of
   each agent and the *connector* layer change.
3. **Latest ecosystem**, pinned: LangChain **1.x (≥1.1)**, LangGraph latest 1.x,
   `langchain-mcp-adapters` ≥0.3.
4. **`deepagents` — out of scope** for now (revisit later for an autonomous Coding agent).
5. Migrate without throwing away the good parts (DB registry, encryption, admin, UI, intake modes).

---

## 1. Dependency changes

Add / bump in `pyproject.toml`:

| Package | Now | Target | Why |
|---|---|---|---|
| `langchain` | *(absent)* | `>=1.1` | provides `create_agent` + `langchain.agents.middleware.*` |
| `langchain-core` | 1.4.6 | latest 1.x | base |
| `langchain-anthropic` | 1.4.5 | latest 1.x | provider |
| `langchain-openai` | 1.3.0 | latest 1.x | provider/gateway |
| `langgraph` | 1.2.4 | latest 1.x | orchestration + interrupts |
| `langgraph-checkpoint-postgres` | 2.x | latest | checkpointer |
| `langchain-mcp-adapters` | *(absent)* | `>=0.3` | load MCP server tools as `BaseTool`s |

Likely **removable later** (once MCP replaces them): bespoke `httpx` issue providers and some
toolkits (see §4). Keep `httpx` (still used elsewhere) and `GitPython` (worktrees) for now.

---

## 2. Agent runtime — adopt `create_agent`

### 2.1 What changes
Each agent's inner loop becomes a LangChain `create_agent` (itself a compiled LangGraph),
**nested inside** our orchestration node. We delete the hand-rolled `bind_tools` loop (the
`agentsys`/PM version) and the ad-hoc single-shot calls in favor of one consistent runtime.

`create_agent(model, tools, *, system_prompt=..., response_format=<PydanticSchema>,
middleware=[...])` gives us, maintained upstream: the ReAct tool loop, automatic tool execution,
**structured output** (`response_format`), retries/error handling, streaming, and the **middleware**
hook (HITL, summarization, fallback).

### 2.2 PM agent: `create_agent` vs custom code — decision
**Decision: use `create_agent` for PM too** (configured minimally), for consistency.

- PM = `create_agent(model, tools=[read-issue tool], response_format=Spec, max_iterations=small)`.
  It reads the issue via the (MCP) read tool and returns a validated `Spec` directly.
- **Benefits of the old custom single-shot code** (honest accounting): slightly more deterministic,
  one fewer dependency, trivially mockable. **Why we still switch:** one runtime for *all* agents,
  free middleware (incl. HITL), structured-output + tools in one call, and no bespoke loop to
  maintain. The determinism gap is closed by a low `max_iterations` and `temperature=0`.
- Net: PM keeps producing a structured `Spec`; only the *plumbing* moves to `create_agent`.

### 2.3 Per-agent runtime matrix

| Agent | Engine | Tools (via MCP unless noted) | `response_format` |
|---|---|---|---|
| **PM** | `create_agent` (minimal) | issue read | `Spec` |
| **Research** | `create_agent` | codebase search/read (MCP filesystem/git) | `ImplementationPlan` |
| **Coding** | `create_agent` (bounded) | filesystem write, git, shell/test | `CodeChange` (or tool-driven) |
| **Reviewer** | `create_agent` | PR/diff read (GitHub MCP) | review verdict schema |
| **Fixer** | `create_agent` (bounded, ties to `MAX_FIX_ITERATIONS`) | git, filesystem, shell | patch result schema |
| **Intake** | not an LLM agent | calls a connector tool programmatically | — |

### 2.4 The node adapter (composition)
`graph/nodes.py` adapter maps `WorkflowState` → inner agent input `{"messages": [...]}` (or
structured input), runs the inner agent, and writes the result into the agent's **namespace**.
Inner agents get **no own checkpointer** — the parent graph's Postgres checkpointer persists
everything (so interrupts/resume work end-to-end). **Validate** subgraph-interrupt bubbling in a
spike (see §6 risks).

---

## 3. Human-in-the-loop — `HumanInTheLoopMiddleware`

- Attach `HumanInTheLoopMiddleware(interrupt_on={...})` to agents that call **dangerous tools**
  (merge PR, push, post-comment). It interrupts *before* executing those tools and resumes via
  LangGraph `Command(resume=...)`, persisted by the checkpointer.
- Keep the existing `Autonomy` config as the **switch**: autonomy on → no interrupts (the gate is a
  no-op); autonomy off → interrupts on the configured tools. `ApprovalGate` becomes a thin policy
  that builds the `interrupt_on` map, not a hand-rolled pause.
- **New API surface (required):** `POST /runs/{id}/resume` with an approve/edit/reject decision →
  issues `Command(resume=...)`. Add an "Approve / Reject" control to the run-status UI.

This is the missing half of our gate: today it only *records* "awaiting human"; with the middleware
it actually pauses and can be resumed.

---

## 4. Connectors — MCP instead of bespoke integrations

### 4.1 Why
Every issue source we hand-wrote already has a maintained MCP server:

| Source | MCP server | Transport |
|---|---|---|
| GitHub | official **GitHub MCP server** (remote hosted, or local) | HTTP (hosted) / stdio |
| Jira | official **Atlassian MCP server** (remote) or community `sooperset/mcp-atlassian` (Cloud + DC) | HTTP / stdio |
| Plane | official **`makeplane/plane-mcp-server`** (Python/FastMCP, `uvx`) | stdio |

MCP gives us **read *and* write** tools (get issue, list issues, comment, create PR, etc.) as
standard `BaseTool`s — usable both programmatically (deterministic intake) and bound into agents
(`create_agent`). That replaces our bespoke `httpx` providers **and** several toolkits with
maintained servers — exactly "don't reinvent the wheel," and future sources are config, not code.

### 4.2 Mechanism
`langchain-mcp-adapters` `MultiServerMCPClient({...}).get_tools()` connects to the configured MCP
servers and returns their tools. Those tools:
- bind into `create_agent(tools=...)` for agent-driven steps (Dev creating PRs, Reviewer reading
  diffs, posting comments), and
- can be invoked directly (`tool.ainvoke({...})`) for the **deterministic intake** fetch.

### 4.3 Reframe `Integration` → `Connector` (repurpose, don't discard)
The DB registry + Fernet encryption + admin/UI we already built are reused — only the row's meaning
changes from "bespoke provider config" to "**MCP server connection**":

| Old `Integration` field | New `Connector` field |
|---|---|
| `kind` (github/jira/plane) | `kind` / `transport` (`http` \| `stdio`) |
| `base_url` | `url` (remote MCP) **or** `command` + `args` (stdio, e.g. `uvx plane-mcp-server`) |
| `config` (repo/project_key/…) | `env` / `headers` (server-specific: repo, workspace slug, etc.) |
| `secret` (encrypted) | `secret` (encrypted) — token passed to the server via env/header |
| `enabled` | `enabled` |

A run still references a connector by id; intake builds the `MultiServerMCPClient` from the row,
loads tools, and (a) calls the read tool for `raw_issue`, (b) hands the toolset to the agents.

### 4.4 What stays custom (honest)
- A **thin per-server normalization** (`tool result → RawIssue`) for the deterministic intake,
  because MCP servers return different shapes. Smaller than today's providers, but not zero.
- **git worktrees** (GitPython) — local FS/branch isolation; keep (or use a filesystem/git MCP +
  worktree wrapper). Not worth replacing now.

### 4.5 Transport / ops decision
Prefer **remote/HTTP MCP endpoints** (GitHub hosted, Atlassian remote) to avoid bundling server
binaries in our image. Use **stdio** (`uvx`/`npx`) only where there's no hosted option (e.g. Plane
via `uvx plane-mcp-server`), which means adding `uv`/`node` to the Docker image for those.

---

## 5. Migration phases (PR-sized)

- **P0 — Spike — ✅ DONE (2026-06-12).** Added `langchain` 1.3.8 + `langchain-mcp-adapters` 0.3.0;
  empirically confirmed `create_agent(model, tools, system_prompt, response_format)` →
  `ainvoke({"messages":[...]})` returns `structured_response`; confirmed `HumanInTheLoopMiddleware`
  + `MultiServerMCPClient` import; confirmed interrupt→resume through the checkpointer (see P4).
- **P1 — Agent runtime seam — ✅ DONE (2026-06-12).** `BaseAgent.build_agent()` constructs a
  `create_agent` (model + `get_tools()` + `system_prompt` + `response_format`); `generate()` runs it
  and returns the validated object. All structured agents (PM/Research/Coding) now use this one
  runtime — the hand-rolled structured-output call is gone. Tests use a `create_agent`-compatible
  fake model. Green.
- **P2 — Convert looping agents — ⬜ TODO.** Give Research/Coding/Reviewer/Fixer real tools
  (`get_tools()` → codebase/git/shell/PR) so `create_agent` actually loops. Behavioral change;
  bounds via `max_iterations`. (Biggest remaining piece.)
- **P3 — MCP connector layer — 🟡 LOADER DONE (2026-06-12; hosted HTTP).** `Connector.transport`
  (`http` = hosted MCP, else built-in httpx); `integrations/mcp.py` builds a `MultiServerMCPClient`
  StreamableHttp connection from the row and `load_mcp_tools()` / `mcp_tools_for(id)` return its
  tools; admin + UI expose `transport`. Tested (config + mocked tool-load + an agent calling an MCP
  tool through `create_agent`). httpx kept as fallback. **Remaining:** bind a run's connector MCP
  tools into the live agents (pairs with P2) + verify against a real hosted server; local stdio
  transport deferred.
- **P4 — HITL — 🟡 MECHANISM DONE (2026-06-12).** `Runner.resume_run` + `POST /runs/{id}/resume`
  land, and interrupt→resume-through-checkpointer is tested. **Remaining:** attach
  `HumanInTheLoopMiddleware` to dangerous tools (depends on P2 tools existing) + wire `Autonomy` →
  `interrupt_on` + a UI approve/reject control.
- **P5 — Retire bespoke code — ⬜ TODO (after P3).** Remove httpx providers/toolkits MCP covers.
- **P6 — Optional middleware — ⬜ TODO.** `SummarizationMiddleware`, `ModelFallbackMiddleware`.

Each phase keeps **ruff + mypy --strict + pytest** green and updates the changelog.

---

## 6. Risks & open questions

1. **Subgraph interrupts** through the parent checkpointer — the P0 must-validate item.
2. **MCP ops:** stdio servers need `uvx`/`node` in the image; remote servers need network + token
   auth plumbing. Adds moving parts vs a 40-line httpx client.
3. **Determinism / cost:** agent loops make more calls — keep `max_iterations` bounds + the
   per-ticket budget guard.
4. **Result normalization** still per-server for deterministic intake (smaller, not gone).
5. **Version churn:** `create_agent`/middleware is newer than langgraph core — pin exact 1.x.
6. **Testing:** inject a fake `BaseChatModel` into `create_agent`; for MCP, mock the loaded tools
   (don't spin real servers in unit tests). One integration test per real connector, opt-in.
7. **Plane MCP** is `uvx`-based (stdio) — confirm it runs headless in our container.

---

## 7. What we keep vs replace vs remove

| Keep | Replace (wheel exists) | Remove (after migration) |
|---|---|---|
| Orchestration `StateGraph`, namespaced `WorkflowState` | Hand-rolled tool loop → `create_agent` | bespoke `integrations/{github,jira,plane}.py` |
| Postgres checkpointer | `ApprovalGate` pause → `HumanInTheLoopMiddleware` + resume endpoint | toolkits covered by MCP (`git`/`pr`/`codebase`/`shell`) |
| DB registry + Fernet encryption + admin + UI (repurposed → `Connector`) | bespoke issue providers → **MCP servers** via `langchain-mcp-adapters` | — |
| `get_chat_model` (provider-agnostic) | — | — |
| intake modes + conditional routing | — | — |

---

## 8. Decision summary

- **`create_agent`:** adopt for **all** agents incl. PM (minimal). Custom code's only edge is
  marginal determinism/simplicity — outweighed by one consistent, middleware-capable runtime.
- **`HumanInTheLoopMiddleware`:** adopt for dangerous-tool approvals; needs a resume endpoint.
- **`deepagents`:** **not now.**
- **MCP connectors:** adopt as the connector layer (GitHub/Jira/Plane all have MCP servers);
  reframe `Integration` → `Connector`, reuse DB/encryption/admin/UI; retire bespoke providers.

## 8b. PM agent v2 — expanded responsibilities

The PM agent grows from "issue → spec" into the planning hub. Three responsibilities:

### R1 — Ingest requirements from many sources (incl. uploaded files)
- Inputs: raw issue text (from a connector), **and/or uploaded files** — `pdf`, `md`, `doc/docx`,
  `txt`, `pptx`, `xlsx`, `html`, …
- **File→text via a `documents` reader.** Library choice: **`markitdown`** (Microsoft, MIT,
  multi-format → Markdown in one dependency) as the primary; LangChain community loaders
  (`pypdf`/`python-docx`/`unstructured`) are the fallback for formats it misses. Rationale: one
  robust dep covering "all sorts of files" beats wiring N per-format loaders.
- Files arrive via an **upload path**: an `attachments` list on the run (file paths today;
  upload endpoint + object storage later). PM reads each → concatenated Markdown context.

### R2 — Two modes: create spec, then create tickets and push them
- **Mode A (raw → spec):** if requirements are raw, generate a structured `Spec` (today's behavior),
  publish to the Board for oversight.
- **Mode B (spec → tickets → task sink):** whether the spec was *provided* (a `spec_ready` upload)
  or *just generated*, PM breaks it into Tickets/tasks and **pushes them to the user's preferred
  task tool**: **Plane / Jira / Google Sheet / …** (default = file board).
- **`TaskSink` abstraction** (`publish_tickets(spec) -> list[ref]`):
  - `FileBoardSink` (default, exists) — Markdown/JSON to `runtime/`.
  - `PlaneSink` / `JiraSink` — **via the MCP connector's write tools** (`create_issue`), reusing the
    §4 connector layer (no bespoke API client).
  - `GoogleSheetsSink` — via a Sheets MCP server or the Sheets API connector (later).
  - The sink is chosen per project/connector (`task_sink:` config), like issue sources.

### R3 — Spikes: PM can defer work to Research
- PM may mark a ticket as a **spike** (needs investigation before implementation).
- Schema: add `TicketType.spike` **and** a `needs_research: bool` flag on `Ticket`.
- Routing: tickets flagged `needs_research`/`spike` are handed to the **Research agent** (which
  already produces an `ImplementationPlan`); its output can feed back as ticket detail before Coding.
  In the graph this is a conditional hand-off, consistent with the intake routing.

### PM v2 runtime (with `create_agent`)
PM = `create_agent(model, tools=[read_attachments, read_board_item, create_ticket(sink)],
response_format=Spec, max_iterations=small)`:
- `read_attachments` → markitdown over the uploaded files.
- `read_board_item` → the connector's issue read (MCP).
- `create_ticket` → the selected `TaskSink` (MCP write tool for Plane/Jira/Sheets, or file board).
- `response_format=Spec` keeps the structured spec; ticket push happens via the sink tool / a
  deterministic post-step.

### New/changed surfaces
- `schemas.py`: `TicketType.spike`, `Ticket.needs_research`.
- `documents/` (new): multi-format file reader (markitdown).
- `sinks/` (new) or extend `clients/board.py`: `TaskSink` interface + `FileBoardSink`; Plane/Jira/
  Sheets sinks via connectors.
- `WorkflowState`: `attachments: list[str]`, and ticket→spike routing fields.
- API/UI: accept `attachments` (and a `task_sink`/connector) on a run; upload endpoint later.
- New deps: `markitdown` (+ optional loader extras).

### Open decisions (locked via the questions accompanying this plan)
- Ticket push = **MCP write tools** (preferred, per §4) vs dedicated sink clients.
- Google Sheets scope (now vs later).
- File upload = endpoint+storage now vs read-from-path now.
- Sequencing: land PM v2 on the current runtime first, or do the `create_agent`/MCP migration first.

## 10. Agent requirements — full team spec (the "software house" staff)

> Locked from the client brief (2026-06-15). Every agent follows the **same loop-engineering
> contract**: one `create_agent` runtime (`BaseAgent.build_agent()`), structured `response_format`,
> bounded iterations, MCP/toolkit tools, and an **optional HITL gate** that is a config toggle — never
> a scattered `if human:`. "Loop engineering" here = give the agent real tools + a tight objective +
> bounded `max_iterations`, let `create_agent`'s ReAct loop drive, and verify with deterministic
> checks outside the LLM (as PM already does with `validate_spec`).

### 10.0 Cross-cutting: trigger modes (auto vs manual) — **new requirement**
Every agent must support two operating modes, chosen by config (not code):
- **Auto** — the agent acts as soon as it detects assigned/available work (a new ticket, a fresh PR,
  new review comments). Implemented via the existing scheduler hook + connector polling / webhook
  triggers; the orchestration graph routes the work item to the agent node automatically.
- **Manual** — the agent waits for an explicit human trigger (UI button / API call) before it runs.

Add a per-agent `trigger: auto | manual` setting to `projects/<name>.yaml` under a new `agents:` map,
resolved through `pydantic-settings` with env overrides (`AGENT_<NAME>__TRIGGER`). The graph's
intake/dispatch layer consults this to decide whether to fan work out immediately or park it in an
"awaiting trigger" state surfaced in the UI. This composes with `Autonomy` (HITL) — `trigger` governs
*when work starts*; `Autonomy` governs *whether a human approves dangerous steps mid-loop*.

### 10.1 PM agent (planning hub) — **built (v2), extend**
- **Role:** turn requirements into a structured `Spec` (epic + technical spec + tickets + risks) and,
  on approval, publish tickets to the chosen sink.
- **Sources (read):** raw issue text from **any** connector (GitHub/Jira/Plane/Bitbucket/GitLab/
  Confluence/…), **and/or** uploaded files (`pdf/md/doc/docx/txt/pptx/xlsx/html`) via the
  `documents` reader (markitdown primary). All sources are MCP-backed connectors or attachments —
  never hardcoded.
- **Destinations (write):** spec → Board (file today; Jira/Plane/Confluence/raw-md later);
  tickets → `TaskSink` (file / Jira / Plane / Sheets) via MCP write tools.
- **HITL:** mandatory review gate before push (`pm_publish` node → `interrupt("spec_review")`); UI
  shows full spec, human Approves/Rejects/Edits. Already implemented.
- **Persistence:** spec + tickets + refs saved to DB (extend `RunRecord` / add `SpecRecord`); PM runs
  listed paginated + searchable in UI (§12).
- **Runtime:** `create_agent(tools=[read_attachments, read_board_item, create_ticket(sink)],
  response_format=Spec, max_iterations=small)` + deterministic `validate_spec` self-repair.
- **To build:** persist specs to DB for the searchable PM-runs view; wire spec-board publish to
  Jira/Plane/Confluence (not just file); confirm multi-source ingest (Bitbucket/GitLab/Confluence)
  through the MCP connector layer.

### 10.2 Research / Spike agent — **built, extend**
- **Role:** given a spec/RFC/story from any source, do comprehensive investigation and produce
  **research documentation** (today: `ImplementationPlan`; extend to a richer research doc).
- **Tools:** `CodebaseToolkit` (Chroma semantic search + ripgrep + read_file) over a per-run worktree;
  add web/doc lookup tools as needed.
- **Destination (configurable):** research output can be published to (a) an **md file**, (b) a
  **connector comment** (Jira/Plane/Trello/Monday/Linear/… via MCP write tool), or (c) **a new story
  ticket** — in which case PM can pick it up and create a story. New `research_sink` config, modeled
  like `task_sink`.
- **HITL:** optional review of the research doc before it is posted (Autonomy toggle).
- **Trigger:** auto when a ticket is flagged `needs_research`/`spike`; manual otherwise.
- **To build:** `ResearchSink` abstraction (md / comment / ticket); richer research-doc schema;
  feedback loop into PM for spike→story.

### 10.3 Dev (Coding) agent — **built (v1), harden**
- **Role:** take an assigned task and implement it. Inputs: story description + RFC/linked resources +
  actual code + **project skills** (`skills/<name>/SKILL.md`). Output: a PR.
- **Behavioural requirements (locked):**
  1. Generate code **strictly relevant to the task** — nothing extra.
  2. **Create and pass test cases** (run the project's test command in the worktree; loop until green
     within bounds).
  3. Follow the repo's **commit-message convention** if one exists (detect via recent `git log` /
     `CONTRIBUTING`/`commitlint`), else best-practice conventional commits.
  4. **Open a PR** with a proper description; follow the repo **PR template** if present
     (`.github/PULL_REQUEST_TEMPLATE*`), else a comprehensive structured description.
  5. If a **ticket number** exists, prefix commit message **and** PR title with it.
- **Tools (P2 — the big gap):** filesystem write, git, shell/test runner, PR create (gh / GitHub
  MCP). Currently Coding is single-shot (no real loop); convert to a **bounded `create_agent` loop**
  so it can edit → test → fix → re-test before opening the PR.
- **HITL:** optional approval before push/PR-open and before merge (Autonomy + `HumanInTheLoopMiddleware`).
- **To build:** real tool loop (P2), test-runner tool + green-gate, commit/PR convention detection,
  PR-template detection, skills loading into the prompt.

### 10.4 Reviewer agent — **STUB → build (P2)**
- **Role:** review the PR a Dev agent opened; post inline + summary comments.
- **Behavioural requirements (locked):**
  1. Review **in depth in one pass** — avoid review cycles.
  2. Tag each finding by **severity:** `nit` (nice-to-have) / `low` / `medium` / `high` / `critical`.
  3. If everything is perfect → **Approve** the PR.
  4. If an **auto-merge policy** is set → **merge** only when approved.
- **Tools:** PR/diff read, post-review-comment, approve, merge (GitHub MCP / `gh`); read-only codebase
  tools for context.
- **Runtime:** `create_agent(tools=[read_pr_diff, read_file, post_review, approve_pr, merge_pr],
  response_format=ReviewVerdict, max_iterations=small)`. New schema `ReviewVerdict { summary,
  findings: [{path, line, severity, comment, category}], verdict: approve|request_changes, auto_merge_ok }`.
- **HITL:** merge is a dangerous tool → gated by Autonomy (`require_human_for_merge`) +
  `HumanInTheLoopMiddleware`; auto-merge only if policy allows **and** gate passes.
- **Maker/checker:** must be a **different agent instance** from Dev (separate model/prompt allowed).

### 10.5 Fixer agent — **STUB → build (P3); = Dev agent in fix mode**
- **Role:** consume Reviewer comments, fix them, update the PR, resubmit for review.
- **Behavioural requirements (locked):**
  1. Triggered as soon as review comments are posted (auto) or on manual trigger.
  2. Ensure **no linting issues** (run the integrated linter; ruff here, repo's linter elsewhere).
  3. Ensure **tests don't break** (run test suite, loop until green within `MAX_FIX_ITERATIONS`).
  4. **Update the PR description** to reflect the latest changes.
- **Implementation:** reuse the Dev agent's tool loop with a "fix from review feedback" prompt and the
  same worktree/branch; bound by `MAX_FIX_ITERATIONS`. Reviewer ↔ Fixer form the review loop (capped
  to avoid infinite cycles — that's why Reviewer reviews in one deep pass).
- **HITL:** optional approval before re-push (Autonomy toggle).

### 10.6 RFC agent — **placeholder only (build last)**
- **Role (future):** generate RFCs from requirements before PM. **Skip implementation now.**
- **UI:** add a disabled/placeholder card in the agents view labelled "RFC agent — not yet built".

### 10.7 Intake agent — **built**
- Not an LLM agent; fetches the work item from the source connector (MCP read tool or legacy provider)
  and normalizes to `RawIssue`. Drives the auto-trigger dispatch (§10.0).

### 10.8 Agent build matrix (status → target)

| Agent | Status | Loop? | Key tools to add | New schema | HITL gate |
|---|---|---|---|---|---|
| PM | built v2 | minimal | spec-board write (Jira/Plane/Confluence) | — (Spec) | spec review (done) |
| Research | built | yes | research sinks (md/comment/ticket) | richer research doc | optional doc review |
| Dev/Coding | built v1 | **no → yes (P2)** | fs/git/shell/test/PR, skills, convention detect | — (CodeChange) | push/PR + merge |
| Reviewer | **stub (P2)** | yes | PR diff/comment/approve/merge | `ReviewVerdict` | merge |
| Fixer | **stub (P3)** | yes | = Dev + linter + PR-desc update | patch result | re-push |
| RFC | **placeholder** | — | — | — | — |
| Intake | built | n/a | — | RawIssue | — |

---

## 11. Connector overhaul — simpler model, better UX, MCP-first

> Client asks: connectors "feel like very complicated stuff"; make them best-practice, support
> **unlimited** platforms via their **MCP servers**, keep **legacy integrations as a visible backup**,
> let a connection be **source / destination / both**, and keep the source/sink/MCP toggles coherent.

### 11.1 Backend simplifications
1. **MCP is the default path; legacy httpx is explicitly "backup".** Keep `transport` but make its
   meaning obvious in code + UI: `mcp_http` (preferred, unlimited platforms) vs `builtin` (legacy
   GitHub/Jira/Plane httpx, kept for resilience and shown as such in admin).
2. **Validate config per `kind` with discriminated Pydantic models** instead of free-form JSON — catch
   misconfig at create-time, drive the UI form fields, and remove the "errors appear at runtime"
   problem flagged in exploration.
3. **Coherent role toggles** enforced by a model validator: `is_default_sink ⇒ is_sink`;
   `mcp_http ⇒ base_url set`; `is_source` requires a source-capable kind/tool; surface a clear error
   in admin + UI rather than failing mid-run.
4. **Persist `task_sink_id` on `RunRecord`** (today it's ephemeral) so PM-run history has a full audit
   trail.
5. **Connection health check** — a `GET /connectors/{id}/health` that loads MCP tools (or pings the
   legacy provider) and reports ok/error; shown as a status dot in the UI.
6. **MCP server discovery** — optional helper that lists a server's available tools so the user can see
   what a connector can actually do (read vs write capabilities → auto-suggest `is_source`/`is_sink`).

### 11.2 UI (see §12 for the shared design system)
- A dedicated **Connectors** section (not just a docs page): card/table list with health dot, kind
  icon, source/sink/both badges, enabled toggle, MCP-vs-legacy badge.
- **Add-connector wizard:** pick platform → choose MCP (paste server URL + token) or legacy → form
  fields driven by the per-kind schema → **Test connection** → save. Inline help, no raw JSON editing
  for common kinds (raw JSON stays as an "advanced" escape hatch).
- Legacy/backup integrations live under an **"Advanced / legacy"** group, visible in admin for parity.

---

## 12. UI overhaul — Jira-style, server-rendered (HTMX + Tailwind + Alpine)

> **Decision (2026-06-15):** keep FastAPI + Jinja2 as the backbone; add **Tailwind CSS** for a
> Jira-grade design system, **HTMX** for partial updates + SSE-driven live run status, and **Alpine.js**
> for local interactivity (tabs, dropdowns, modals). No SPA, no Node build target — Tailwind via the
> Play CDN initially, with a documented migration path to the Tailwind standalone CLI (single static
> binary, still no Node) if/when we want purged production CSS. Rationale: fastest path to a polished UI
> that reuses existing routes, keeps one Python codebase, and gives real-time updates the current
> `setTimeout` polling can't.

### 12.1 Information architecture (Jira-inspired)
A persistent **left sidebar** + top bar replaces the current thin header nav:
- **Top bar:** product mark, global search (runs/specs/connectors), project switcher, "+ New" menu,
  links to Admin & API docs.
- **Left sidebar sections:**
  - **Dashboard** — at-a-glance: active runs, pending HITL approvals, recent specs, connector health.
  - **Work** — the run/work board (Jira-board feel): columns by stage (Intake → PM → Research → Dev →
    Review → Fix → Done) or a filterable list; each card is a run/ticket.
  - **PM runs** — paginated + **searchable** list of PM runs (client requirement) with spec preview.
  - **Agents** — one card per agent (PM/Research/Dev/Reviewer/Fixer + RFC placeholder) showing status,
    trigger mode (auto/manual) toggle, HITL toggle, model, and a manual "Run" button.
  - **Connectors** — the §11.2 connectors section.
  - **Approvals** — queue of runs paused at a HITL gate (spec review, merge approval) with Approve/
    Reject/Edit.
  - **Admin** (→ SQLAdmin) / **API** (→ /docs).

### 12.2 Shared design system (kill the inline-CSS sprawl)
- New `web/templates/_layout.html` base with Tailwind + HTMX + Alpine includes and a small set of
  reusable partials/macros: `_sidebar.html`, `_topbar.html`, `_card`, `_badge`, `_pill`, `_table`,
  `_modal`, `_tabs`, `_pagination`, `_empty_state`, `_toast`. Light/dark theme via Tailwind + a CSS-var
  palette (keep the current dark palette as the dark theme).
- Replace every template's inline `<style>` with Tailwind utility classes + the shared macros.
- Accessibility pass: ARIA on interactive controls, keyboard nav, focus states.

### 12.3 Real-time + interactivity
- **SSE endpoint** `GET /runs/{id}/events` streaming run-state changes; run-status page subscribes via
  HTMX SSE extension → live agent progress, no full-page polling.
- HTMX partials for: approve/reject (no page reload), pagination, search-as-you-type on PM runs,
  connector test/health, enabling/disabling agents/connectors.
- Alpine for tabs (spec overview/technical/tickets/risks), modals (add-connector wizard, ticket
  detail), and the "+ New" menu.

### 12.4 Pages to (re)build
| Page | Current | Target |
|---|---|---|
| Shell (layout/nav) | thin header, inline CSS | sidebar + topbar, Tailwind design system |
| Dashboard | basic tables | widgets: active runs, approvals queue, connector health, recent specs |
| Work board | — (none) | Jira-style board / filterable list of runs+tickets by stage |
| PM runs | part of runs list | dedicated paginated + **searchable** list w/ spec preview |
| Run detail | one huge page | tabbed, SSE-live agent timeline, inline HITL controls |
| Agents | — (none) | per-agent cards: status, trigger toggle, HITL toggle, model, manual run |
| Connectors | docs page | wizard + health + role/MCP badges (§11.2) |
| Approvals | inline only | dedicated queue |
| Admin | default SQLAdmin theme | keep; light theming pass |

---

## 13. Execution roadmap — next steps & build order

> Client delegated the order ("agents first or UI first — up to you"). **Chosen order: UI foundation
> first, then agents, interleaved.** Rationale: (1) the UI is the client's oversight surface and item
> #1 of the brief; (2) the shared layout/design system is a dependency of *every* other page, so it
> unblocks the most work; (3) it's low-risk and doesn't touch the agent loop (honoring design rule #5 —
> we're adding *visibility*, not new triggers/sinks ahead of a trustworthy loop); (4) Reviewer/Fixer
> (the real agent gaps) need the Approvals/PR UI to be usable anyway. We build the UI shell, then grow
> agents and connector UX against it.

**Progress (2026-06-15):** ✅ done — **U0, U1, U2, U3, Work board, A0, A2, A3, A5**, the **A4 config
layer** (per-agent `trigger`), the **C1 coherence-check slice** (`validate_connector`:
default-sink⇒sink, MCP⇒base_url, source/sink kind-capability — enforced in `create_connector`) and
the **connectors UI port** (role/MCP badges + inline validation status). All shipped steps kept
ruff + mypy --strict + pytest green (110 tests).

**Progress (2026-06-16):** ✅ done — **A1** (Dev tool loop / P2: `DevToolkit` + bounded outer
test-fix loop in `CodingAgent`, `detect_test_command`/`detect_commit_convention`/`read_pr_template`
in `code_intel`), **light/dark theme toggle** (CSS custom properties, FOUC prevention, Alpine
toggle), **A4 dispatch** (`AgentPolicy.trigger` default changed to `"auto"`; `_trigger_gate()`
helper on `BaseAgent` — calls `langgraph.types.interrupt` when `trigger="manual"`, resumes via
`POST /runs/{id}/trigger` with `decision="run"`; gate wired into Research, Coding, Reviewer, Fixer
`run()` methods), **P4b** (Reviewer adds `interrupt({"reason":"merge_approval",...})` before
`_merge()` when `auto_merge_on_approve=True` AND `require_human_for_merge=True`; existing
`/ui/runs/{id}/approve` resumes with `"approve"`), **C1 rest** (per-kind discriminated config
schemas — `GitHubConnectorConfig`, `JiraConnectorConfig`, `PlaneConnectorConfig`,
`MCPHTTPConnectorConfig`, `FileConnectorConfig` — in `schemas.py`; `GET /ui/connectors/{id}/health`
+ HTMX `health-dot` fragment endpoint; Alpine multi-step add-connector wizard in `connectors.html`;
`POST /ui/connectors` create endpoint; `mcp_tools_for_url` for pre-save health preview), **RFC**
(real `RFCAgent` + `RFCDocument` schema + `to_markdown()`; opt-in via `agents.rfc.trigger: auto`;
`RFCState` in `WorkflowState`; `rfc` node added between `pm_publish` and `research` in graph).
140 tests, ruff + mypy --strict clean (71 source files). ⬜ remaining — none (all roadmap items
shipped).

**Sequenced steps (each keeps ruff + mypy --strict + pytest green; update changelogs):**

- **U0 — UI foundation (START HERE).** Add Tailwind/HTMX/Alpine to `_layout.html`; build sidebar +
  topbar + shared macros; port the dashboard to the new shell. No behavioural change. *(this turn)*
- **U1 — Run detail + SSE + Approvals.** SSE run-events endpoint; rebuild run-detail as a live tabbed
  timeline; dedicated Approvals queue wired to `POST /runs/{id}/resume`.
- **U2 — PM runs view (DB-backed).** Persist specs to DB; paginated + searchable PM-runs list with
  spec preview. (Pairs with A0.)
- **U3 — Agents view.** Per-agent cards: status, **trigger auto/manual toggle** (§10.0), HITL toggle,
  model, manual run button; RFC placeholder card.
- **C1 — Connectors overhaul.** Discriminated per-kind config schemas + validators (§11.1); connectors
  section + add-connector wizard + health check (§11.2); persist `task_sink_id` on `RunRecord`.
- **A0 — PM persistence + multi-source.** Save specs/tickets to DB; spec-board publish to Jira/Plane/
  Confluence; verify Bitbucket/GitLab/Confluence ingest via MCP.
- **A1 — Dev agent loop (P2).** Convert Coding to a bounded `create_agent` tool loop: fs/git/shell +
  **test-runner green-gate**, commit/PR-convention + PR-template detection, ticket-number prefixing,
  skills loading. Bind connector MCP tools into live agents.
- **A2 — Reviewer agent (P2).** `ReviewVerdict` schema; deep one-pass review, severity tags, inline +
  summary comments, approve, policy-gated auto-merge with HITL.
- **A3 — Fixer agent (P3).** Dev-in-fix-mode: consume review comments, lint-clean, tests-green, update
  PR description, resubmit; bounded by `MAX_FIX_ITERATIONS`.
- **A4 — Trigger dispatch (auto/manual).** Wire `agents.<name>.trigger` config → graph dispatch +
  scheduler/webhook auto-trigger; "awaiting trigger" state in UI.
- **A5 — Research sinks + spike loop.** `ResearchSink` (md/comment/ticket); spike→story feedback to PM.
- **P4b — HITL middleware activation.** Attach `HumanInTheLoopMiddleware` to dangerous tools (merge/
  push/comment); `Autonomy → interrupt_on`; resume controls already in UI.
- **X — RFC agent.** Build last (placeholder until then).

This roadmap supersedes the older P2–P6 ordering in §5 (those phases are folded into A0–A5/P4b above);
§5 remains the historical record of what shipped (P0/P1/P3-loader/P4-mechanism).

---

## 9. Sources
- create_agent / middleware / HITL: https://docs.langchain.com/oss/python/langchain/agents ·
  https://reference.langchain.com/python/langchain/agents/factory/create_agent ·
  https://www.langchain.com/blog/agent-middleware
- MCP adapters: https://github.com/langchain-ai/langchain-mcp-adapters ·
  https://docs.langchain.com/oss/python/langchain/mcp
- MCP servers: https://github.com/atlassian/atlassian-mcp-server ·
  https://github.com/sooperset/mcp-atlassian · https://github.com/makeplane/plane-mcp-server
