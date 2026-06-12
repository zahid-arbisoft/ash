# Plan — Agent runtime (create_agent + middleware) & MCP connectors

> **Status:** IN PROGRESS — **P0 + P1 done, P4 mechanism done** (2026-06-12). P2 (agent tools),
> P3 (MCP, needs live infra), P4 middleware activation, P5/P6 remain. See §5 for per-phase status.
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
- **P3 — MCP connector layer — ⬜ TODO (partly unverifiable here).** `connectors/mcp.py` builds a
  `MultiServerMCPClient` from a `Connector` (kind=`mcp`, transport http/stdio); load tools for
  agents + deterministic intake. Needs `uvx`/`node`/network or hosted endpoints — verify with the
  user's infra; unit-test by mocking the loaded tools.
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

## 9. Sources
- create_agent / middleware / HITL: https://docs.langchain.com/oss/python/langchain/agents ·
  https://reference.langchain.com/python/langchain/agents/factory/create_agent ·
  https://www.langchain.com/blog/agent-middleware
- MCP adapters: https://github.com/langchain-ai/langchain-mcp-adapters ·
  https://docs.langchain.com/oss/python/langchain/mcp
- MCP servers: https://github.com/atlassian/atlassian-mcp-server ·
  https://github.com/sooperset/mcp-atlassian · https://github.com/makeplane/plane-mcp-server
