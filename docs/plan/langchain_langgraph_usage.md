# LangChain / LangGraph Ecosystem — What We Use and What's Pending

> **Scope:** explains every place the LangChain / LangGraph stack is used in `src/ash/`, the
> design decisions behind each choice, and what is still pending. Keep this file up-to-date
> whenever a new primitive is adopted or a pending item ships.
>
> **Versions** (from `pyproject.toml`):
> `langchain ≥1.1`, `langchain-core ≥1.0`, `langgraph ≥1.0`,
> `langgraph-checkpoint-postgres ≥2.0`, `langchain-mcp-adapters ≥0.3`,
> `langchain-anthropic ≥1.0`, `langchain-openai ≥1.0`, `langchain-community ≥0.3`

---

## 1. LangGraph — Orchestration & State

### 1.1 Graph topology (`graph/builder.py`)

```
START → intake → (route) → pm → pm_publish → rfc → plan_stories
                          ↘ (spec_ready / raw_to_dev shortcut)
                                             ↓
                                        story_router ⟵────────────┐
                                             ↓ (next story exists)  │
                                        story_build (subgraph)      │
                                             │                      │
                                    research → coding → reviewer → fixer → story_finalize
                                                                           └──────────────┘
                                             ↓ (no more stories)
                                           merge → END
```

**Two-level graph design:**

| Level | Type | Checkpointer | Purpose |
|-------|------|-------------|---------|
| Root graph | `StateGraph(WorkflowState)` | `AsyncPostgresSaver` | Entire run — PM, RFC, story routing, merge |
| Story subgraph | `StateGraph(WorkflowState)` | None (inherits parent) | One story: research → coding → reviewer → fixer |

The story subgraph is compiled **without its own checkpointer** (`sub.compile()`) so it shares
the parent run's Postgres checkpoint. State is written to the same thread on every step.

**Conditional routing:**
- `_route_after_intake` (line ~247): maps `intake_mode` → `{pm | plan_stories | merge}`
  - `raw_to_spec` / `spec_ready` → pm (PM generates or extracts the spec)
  - `raw_to_dev` → plan_stories (skip PM entirely)
- `_route_story` (line ~256): after each story step checks `next_story(state)`
  - next story exists → story_build
  - all stories done → merge (END path)

---

### 1.2 `WorkflowState` and reducers (`graph/state.py`)

`WorkflowState` is a **namespaced state** class where each agent owns its own substate:

```python
class WorkflowState(BaseModel):
    # ── run-level fields ──────────────────────────────────────
    run_id: str
    project: str
    intake_mode: IntakeMode
    story_mode: StoryMode          # "single" | "multiple"
    status: Literal["running", "completed", "failed", "cancelled"]

    # ── agent namespaces (one substate per non-story agent) ──
    intake:   IntakeState
    pm:       PMState              # spec, board_ref, ticket_refs, story_selection
    rfc:      RFCState             # rfc_doc
    research: ResearchState        # flat scratch, overwritten per story
    coding:   CodingState          # flat scratch, overwritten per story
    reviewer: ReviewerState        # flat scratch, overwritten per story
    fixer:    FixerState           # flat scratch, overwritten per story

    # ── per-story fan-out (decision #26) ─────────────────────
    stories:     Annotated[dict[str, StoryState], merge_stories]  # reducer
    story_order: list[str]         # dependency-sorted ticket ids
    current_story: str             # sequential cursor
```

**The `merge_stories` reducer** (`state.py:138`) is the key primitive for the fan-out pattern:

```python
def merge_stories(old: dict, new: dict) -> dict:
    return {**old, **new}   # new wins on collision — safe for sequential updates
```

LangGraph calls this reducer every time a node returns `{"stories": {...}}`, merging the
incoming story update into the existing dict without clobbering unmodified stories. This is
the standard LangGraph reducer pattern for maps with independent-keyed entries.

**Per-story scratch namespaces** (research, coding, reviewer, fixer) are **flat fields at the
root level**, not inside `stories`. Before each story step, `_hydrate_story(state)` copies the
story's saved namespace into the flat scratch so the agent sees the right context; after the
step, `_fold_story()` writes the result back into `stories[current_story]`. This two-way
translation means existing agent code needs zero changes for fan-out.

---

### 1.3 Checkpointer — `AsyncPostgresSaver` (`graph/checkpointer.py`)

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver, JsonPlusSerializer

checkpointer = AsyncPostgresSaver.from_conn_string(dsn, serde=_SERDE)
```

A **custom `_SERDE`** registers every Pydantic model used in state so they survive the
JSON → Pydantic round-trip inside the checkpoint:

```python
_SERDE = JsonPlusSerializer(
    custom_serializer=...,
    custom_deserializer=...,
    # registered models: WorkflowState, PMState, Spec, Ticket, StoryState, CodeReview, ...
)
```

Without the custom serde, Pydantic models would be stored as dicts and fail to deserialize on
resume. Every new state model added to `WorkflowState` must also be registered here.

**Thread isolation:** each run is stored under `configurable.thread_id = run_id`. The runner
always calls `graph.ainvoke / aupdate_state` with `{"configurable": {"thread_id": run_id}}`.

---

### 1.4 `interrupt()` / `Command(resume=...)` — Human-in-the-loop gates

Three interrupt points exist in the graph, each implementing a HITL gate:

| Location | Interrupt value | Resume value | Purpose |
|----------|----------------|-------------|---------|
| `pm.py` `PMPublishAgent.run()` | `"spec_review"` | `"approve"` / `"reject"` / `{"action":"approve","stories":["T1"]}` | Hold before pushing tickets to Jira/Plane; human reviews spec |
| `base.py` `_trigger_gate()` | `{"reason":"manual_trigger","agent":"<name>"}` | `"run"` | Hold before any agent marked `trigger: manual`; human triggers from UI |
| `reviewer.py` | `{"reason":"merge_approval",...}` | `"approve"` / anything else | Hold before auto-merging a PR |

**How resume works** (`runner.py:resume_run`):

```python
await self._graph.ainvoke(
    Command(resume=decision),           # LangGraph resumes from the interrupt point
    config={"configurable": {"thread_id": run_id}},
)
```

`Command(resume=value)` is the canonical LangGraph primitive for resuming an interrupted run.
The checkpointer replays the graph from the last checkpoint; the `interrupt()` call returns
`value` and execution continues.

**Interrupt detection** (`runner.py:get_run`): reads `snapshot.interrupts[0].value` from the
Postgres checkpoint and maps it to `pending_review`, `pending_trigger`, or `pending_merge` in
the UI-facing state dict. A background-task guard suppresses the interrupt overlay while a
resume task is actively running (prevents the button reappearing mid-execution).

---

### 1.5 `aupdate_state` — Retry / rewind (`runner.py:retry_run`)

For retrying a failed story or re-running the PM node, the runner uses:

```python
await self._graph.aupdate_state(
    config,
    update={run_step: _fresh_substate(run_step), "status": "running"},
    as_node=predecessor,          # LangGraph replays from this node
)
```

`as_node=predecessor` tells LangGraph to treat the update as if it was written by the node
*before* the one we want to re-run, so the graph re-enters the correct node on the next
`ainvoke`. For story retries, `as_node="plan_stories"` so `story_router` picks up the reset
story and routes into `story_build` again.

**PM workbench variants (decision #29):**
- `Runner.regenerate_spec` seeds a fresh `PMState(feedback=…, regeneration_count=n+1)` and forks
  `as_node="intake"`, then `_drive` re-runs `pm → pm_publish` so the spec is regenerated from the
  reviewer's feedback and re-interrupts at the gate. (It can't reuse `retry_run(from_step="pm")`,
  whose `_fresh_substate("pm")` would wipe the feedback before PM reads it.)
- `Runner.refine_ticket` patches `pm.spec` in place with `aupdate_state(config, {"pm": …})` **and no
  `as_node`** — it does NOT advance the graph, so the run stays paused at the `spec_review` interrupt.
  One caveat: a plain `update_state` while interrupted drops `snapshot.interrupts`, so `get_run`
  falls back to `snapshot.next` (the `pm_publish` node still being "next") to keep the gate visible.

**Conditional routing for standalone PM** (`graph/builder.py`): the `pm_publish → rfc` and
`rfc → plan_stories` edges are conditional (`_route_after_pm_publish` / `_route_after_rfc`). Full
runs (`pm_only=False`) always return their original targets; a `pm_only` run routes to `merge`
(END) by default, or to a one-shot `rfc` / `plan_stories` when `PMState.next_action` is set from the
gate decision. This is the seam the planned "every agent in its own space" work will extend.

---

## 2. LangChain Core — Agent Contracts and Primitives

### 2.1 `BaseAgent` and `create_agent` (`agents/base.py`)

Every agent inherits `BaseAgent` which wraps LangChain's `create_agent`:

```python
from langchain.agents import create_agent

def build_agent(self, system_prompt, tools, response_format=None):
    return create_agent(
        model=self.get_model(),
        tools=tools,
        system_prompt=system_prompt,
        response_format=response_format,
    )
```

`create_agent` builds a ReAct-style agent that alternates tool calls and model calls. ASH
uses it for the *explore* phase of `generate()` — the agent browses the codebase, fetches
issues, and collects notes before the extract phase crystallises a structured output.

**Two-phase `generate()`:**

```
generate(schema, system, user, tools)
    │
    ├── if tools → _explore()     ← ReAct tool loop (create_agent)
    │       returns: last_text (free-form notes)
    │
    └── _extract(schema, notes)  ← with_structured_output (no tools)
            returns: validated Pydantic instance
```

Separating exploration (tool loop) from extraction (structured output) avoids the tool-vs-
schema conflict that causes `json_validate_failed` errors on Groq / LiteLLM gateways.
Agents that need no tools (PM, RFC, Reviewer) skip `_explore` and call `_extract` directly.

### 2.2 `with_structured_output` (`agents/base.py:_extract`)

```python
chain = model.with_structured_output(schema, include_raw=True)
result = await chain.ainvoke([SystemMessage(...), HumanMessage(...)])
parsed: T = result["parsed"]   # validated Pydantic instance
raw: AIMessage = result["raw"] # for token metadata
```

`include_raw=True` returns both the validated object and the raw `AIMessage` so token usage
metadata can be extracted for the `AgentRunMetric` DB rows.

**Fallback chain** (when `with_structured_output` fails):

```
LengthFinishReasonError         → retry _extract with shorter _EXTRACT_SYSTEM prompt
json_validate_failed / tool
  call validation / etc.        → retry _extract with shorter _EXTRACT_SYSTEM prompt
_extract also truncates         → raise RuntimeError("Increase LLM_MAX_TOKENS to ≥8192")
403 / blocked / guardrail       → raise GuardrailBlockedError
anything else                   → re-raise
```

### 2.3 Message types used

| Type | Where | Purpose |
|------|-------|---------|
| `SystemMessage` | `agents/base.py` | Agent system prompt |
| `HumanMessage` | `agents/base.py` | User brief / work context |
| `AIMessage` | `agents/base.py` | Model response; tool_calls extracted from here |
| `ToolMessage` | `agents/base.py` | Tool execution result fed back to model in ReAct loop |

### 2.4 LLM factory (`llm/factory.py`)

Provider-agnostic factory returns a `BaseChatModel`:

```python
# Anthropic
ChatAnthropic(model_name=..., temperature=0, max_tokens_to_sample=..., api_key=..., base_url=...)

# OpenAI-compatible (LiteLLM / Ollama / vLLM)
ChatOpenAI(model=..., temperature=0, api_key=..., base_url=..., max_tokens=...)
```

Selecting between them is a single env var: `LLM_PROVIDER=anthropic | openai`.
Per-agent overrides use `AGENT_<NAME>__MODEL` / `AGENT_<NAME>__MAX_TOKENS`.

---

## 3. Tools and Toolkits (`toolkits/`)

### 3.1 Toolkit protocol

```python
class Toolkit(Protocol):
    def get_tools(self) -> list[BaseTool]: ...
```

All toolkits implement this protocol; agents call `toolkit.get_tools()` and pass the list into
`generate(..., tools=toolkit.get_tools())`.

### 3.2 `CodebaseToolkit` — used by Research agent

| Tool | Signature | Backend |
|------|-----------|---------|
| `search_codebase` | `(query, path?, max_results?)` | Chroma semantic search → grep fallback |
| `list_directory` | `(path?, depth?)` | os.walk tree |
| `read_file` | `(path, start_line?, end_line?)` | Line-ranged file read |
| `grep_code` | `(pattern, path?, case_sensitive?)` | subprocess grep |

All are created with `StructuredTool.from_function(coroutine_fn, args_schema=...)` so LangChain
can validate inputs and generate the JSON schema for the tool call.

### 3.3 `DevToolkit` — used by Coding and Fixer agents

| Tool | Signature | Backend |
|------|-----------|---------|
| `read_file` | `(path, start_line?, end_line?)` | Scoped to worktree root |
| `list_files` | `(pattern?)` | glob within worktree |
| `search_code` | `(query)` | substring search |
| `run_command` | `(cmd)` | subprocess, allow-listed prefixes only, 120 s timeout |

`run_command` allow-list (`_ALLOWED_PREFIXES`): `pytest`, `python -m pytest`, `ruff`, `mypy`,
`black`, `isort`, `git status`, `git diff`. Arbitrary shell execution is blocked.

### 3.4 `BoardToolkit` — used by Intake agent

| Tool | Signature | Backend |
|------|-----------|---------|
| `read_board_item` | `(item_id)` | GitHub issues API |
| `post_board_comment` | `(item_id, body)` | GitHub comment API |

---

## 4. MCP Adapters (`integrations/mcp.py`)

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async def load_mcp_tools(connector: Connector) -> list[BaseTool]:
    config = server_config(connector)   # {"transport": "streamable_http", "url": ..., "headers": ...}
    async with MultiServerMCPClient({"server": config}) as client:
        return await client.get_tools()
```

`MultiServerMCPClient` from `langchain-mcp-adapters` (v0.3.0) connects to a hosted MCP server
over HTTP and returns its tools as standard LangChain `BaseTool` instances. Once loaded, they
drop in identically to hand-written tools — no agent code changes needed.

**Connector record → MCP tools:**
- `Connector.transport = "http"` signals an MCP-over-HTTP server
- `Connector.base_url` is the server endpoint
- `Connector.secret` is sent as the `Authorization: Bearer <token>` header
- Extra headers live in `Connector.config["headers"]`

A helper `mcp_tools_for_url(base_url, secret)` lets the connector wizard preview available
tools before the connector is saved to the DB.

---

## 5. Per-Story Fan-out Details (`graph/stories.py`, `graph/nodes.py`)

### 5.1 `plan_stories` node

`build_stories(state)` converts PM's `spec.tickets` into `WorkflowState.stories` (a
`dict[ticket_id, StoryState]`) and `story_order` (dependency-sorted):

```python
stories, order = build_stories(state)
return {"stories": stories, "story_order": order, "current_story": ""}
```

The node writes to the `stories` reducer key, which merges into the existing dict (idempotent —
existing stories keep their status/branch/pr_url on retry).

### 5.2 `story_router` and sequential loop

```python
def story_router(state: WorkflowState) -> dict:
    nxt = next_story(state)           # returns next pending, dep-unblocked story id
    return {"current_story": nxt or ""}

# Conditional edge:
story_router → _route_story:
    current_story != "" → story_build (subgraph)
    current_story == "" → merge
```

`next_story` skips stories whose dependencies are not yet `completed/skipped`, enforcing the
topological order from `topo_order()` (Kahn's algorithm).

### 5.3 Story hydration / folding (`graph/nodes.py`)

```
story_router sets current_story = "T2"
    ↓
_hydrate_story(state):
    state.research = state.stories["T2"].research   # copy saved namespace
    state.ticket_id = "T2"
    ↓
story_build subgraph runs (research → coding → reviewer → fixer)
    ↓
_fold_story(state, agent_name, result):
    stories["T2"].research = result["research"]     # fold result back
    stories["T2"].branch  = result.get("coding.branch") or prior branch
    stories["T2"].status  = "completed" / "failed"
```

This "flat scratch at root, hydrate/fold on entry/exit" pattern lets existing agents remain
story-unaware — they read/write flat `state.research` / `state.coding` as always.

---

## 6. What's Pending

### P1 — Bind connector MCP tools into live agents *(OPEN)*

**What:** `integrations/mcp.py` loads MCP tools but no agent calls `load_mcp_tools()` yet.
**Where to wire:** `BaseAgent.get_tools()` (or each agent's override) should:
1. Load active MCP connectors for the run's project from the DB
2. Call `load_mcp_tools(connector)` for each
3. Merge with toolkit tools before passing to `generate()`

**Blocker:** needs a resolver that fetches connectors inside an async DB session at tool-load
time (agents are constructed before the run starts; tools need the run's connector config).

**Design note:** The simplest approach is to pass the resolved MCP tool list through
`WorkflowState` or through `Runner.start_run → _graph.ainvoke` config so each node can append
them to its toolkit at execution time rather than construction time.

### P2 — `Send` / parallel story fan-out *(DEFERRED)*

**What:** stories currently build **sequentially** (story_router → story_build → story_router).
A parallel variant would use LangGraph's `Send` primitive to dispatch all ready stories at once:

```python
# Hypothetical parallel story dispatch
def plan_stories_parallel(state):
    return [Send("story_build", {**state, "current_story": tid})
            for tid in next_stories_ready(state)]   # all dep-unblocked stories
```

`Send` creates independent graph invocations that run concurrently; their results are merged
via the `merge_stories` reducer.

**Why deferred:** sequential builds are simpler to reason about for HITL gates (one PR at a
time) and avoid worktree conflicts on shared files. Parallel fan-out is in the plan (decision
#26) but the sequential path has met all current needs.

### P3 — LangGraph streaming *(NOT STARTED)*

**What:** expose token-by-token or step-by-step streaming to the UI.

**Options:**
- `graph.astream(input, config)` — yields events as each node finishes (already an SSE stream
  of run-level events exists in the UI, but it polls state rather than streaming graph events)
- `graph.astream_events(input, config, version="v2")` — finer-grained per-token events

**Why not done:** the current SSE stream (`/ui/runs/{id}/events`) polls `get_run()` on a
timer, which is sufficient for current latency. Real streaming would require the runner to
propagate `astream_events` output to the SSE channel and handle mid-stream checkpoint writes.

### P4 — A5 Research sinks *(OPEN)*

**What:** Research agent currently writes its `ImplementationPlan` only to `WorkflowState`.
A sink step would push it to a board/Plane ticket as a comment (like PM pushes the spec to
the task sink).

**Where:** add a `research_publish` node after `research` in the story subgraph, analogous to
`pm_publish`.

### P5 — Instructor fallback *(CONDITIONAL)*

`generate()` currently falls back to `_extract` (a shorter system prompt + `with_structured_output`)
when the primary structured call fails. An alternative final fallback using
[Instructor](https://github.com/jxnl/instructor) (retry + patch) was flagged in the plan.
Not wired yet — `_extract` has been sufficient. Only needed if `with_structured_output`
consistently fails for a specific model.

---

## 7. Dependency Map

```
langgraph                   ← StateGraph, WorkflowState, interrupt, Command, Send (pending)
  └── langgraph-checkpoint-postgres ← AsyncPostgresSaver, JsonPlusSerializer
langchain-core              ← BaseChatModel, BaseTool, StructuredTool, message types
langchain                   ← create_agent
langchain-anthropic         ← ChatAnthropic
langchain-openai            ← ChatOpenAI
langchain-mcp-adapters      ← MultiServerMCPClient (loaded, not yet bound to agents)
langchain-community         ← document loaders (PDF, DOCX) used in ash/documents.py
```

---

## 8. Quick Reference

| What you want to do | LangChain/LangGraph primitive | File |
|---------------------|------------------------------|------|
| Add a new agent | Subclass `BaseAgent`, implement `run()` | `agents/` |
| Add a new graph node | `builder.py` `add_node(name, make_node(agent, name))` | `graph/builder.py` |
| Add a new HITL gate | `interrupt(payload)` in agent, `get_run()` maps payload to UI key | `agents/<x>.py`, `graph/runner.py` |
| Add a new tool to an agent | Subclass toolkit, add `StructuredTool.from_function(...)`, override `get_tools()` | `toolkits/`, `agents/<x>.py` |
| Add a new MCP connector | Create `Connector(transport="http", ...)` in DB, call `load_mcp_tools()` | `integrations/mcp.py` |
| Register a new state model for checkpoint | Add to `_SERDE` registry | `graph/checkpointer.py` |
| Retry a run from a node | `runner.retry_run(run_id, from_step)` → `aupdate_state(as_node=predecessor)` | `graph/runner.py` |
| Add a per-story field | Add to `StoryState`, update `_hydrate_story` and `_fold_story` | `graph/state.py`, `graph/nodes.py` |
