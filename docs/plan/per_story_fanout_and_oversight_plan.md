# Plan — Per-story fan-out, per-story oversight, retry/regenerate & RFC/UI polish

> **Status:** ✅ IMPLEMENTED (2026-06-17) — **F0–F8 shipped** in one pass. 166 pytest green,
> ruff clean, mypy --strict clean (77 source files). Authoritative parent plan is
> `ash_architecture_and_plan.md` (decision **#26** + changelog reference this doc). This doc is the
> detailed design for the "story = unit of execution" restructure and the UI/RFC asks from the
> 2026-06-17 client brief.
>
> **Working agreement:** every implementation change updates this doc + the parent plan's changelog
> in the same turn. Keep ruff + mypy --strict + pytest green at every phase boundary.
>
> **What shipped (file map):** state/reducer/accessors → `graph/state.py`; story planning + topo
> order + cursor → `graph/stories.py`; `plan_stories`/`story_router`/`story_build` subgraph + per-
> story finalize/cleanup → `graph/builder.py`; node-adapter story scoping + analytics capture →
> `graph/nodes.py`; per-story retry/regenerate → `graph/runner.py`; PM single/multiple + gate
> selection → `agents/pm.py`; no-dup PR update → `agents/coding.py`; DB (`StoryRecord`,
> `AgentRunMetric`, `AgentTask.ticket_id`, `RunRecord.story_mode`) → `db/models.py` +
> `db/{stories,metrics,tasks}.py` + `db/base.py` backfills; chunked index + line-range reads →
> `clients/chroma.py` + `clients/code_intel.py` + `toolkits/{codebase,dev}.py`; per-story timeline,
> PR dropdown, RFC markdown preview, analytics chips → `web/templates/*` + `web/routes.py`.

---

## 0. Why this exists (the client brief, distilled)

Brief #1 (2026-06-17):

1. **PM** — a UI control to produce a **single story (default)** or **multiple stories** (§4 — a
   better post-PM story-selection option is proposed there).
2. **Dev/Research/Reviewer/Fixer** — run only when enabled; when a run has multiple stories, each
   story gets its **own PR**, built **one by one**; show **per-PR progress** on the UI.
3. **RFC** — always exactly **one** RFC per run; **Markdown preview** on the UI; fix the
   auto-collapsing widget.
4. **Run header** — show the **PR link(s) top-right** alongside the source link.
5. **Retry on failure is per-story** — if Research/Dev/Reviewer/Fixer fails on story 2 of 3, retry
   resumes **from story 2**; never create a duplicate PR.
6. **Manual per-story regeneration** — re-run Research, regenerate a PR (update, not duplicate),
   re-review, or re-fix **for one specific story**.
7. **LangGraph-first** — model all of this with LangGraph primitives (state + reducers + conditional
   edges + subgraph + checkpointer interrupts/resume). Restructure code to fit LangGraph rather than
   hand-rolling control flow. (Now a non-negotiable rule in `CLAUDE.md`.)

Brief #2 (2026-06-17, this update):

8. **Structured LLM outputs** — use the **LangChain ecosystem** primitive if it covers the need;
   only otherwise fall back to **Pydantic + Instructor** (§4b).
9. **Story dependencies are honoured** — build in dependency order; a story waits for its deps
   (§3 `story_router` + §11.4).
10. **Context minimization (Research→Fixer)** — stop shipping unnecessary project code to the LLM;
    send only what each step needs. §12 documents the **current** implementation and the
    **improvement** plan.
11. **Worktree cleanup when needed** — per-story, on completion/failure/regenerate, not only at the
    end (§11.5 + §12).
12. **Agent analytics** — persist **tokens (in/out)** and **time consumed** per agent (and per
    story) at the **DB level**, and surface them on the **UI** for each agent (§13).

---

## 1. The core problem & the locked architecture

**Today** a run = one unit of work. The graph is linear
(`intake → pm → pm_publish → rfc → research → coding → reviewer → fixer → merge`), and the
build-team namespaces (`research/coding/reviewer/fixer`) are **flat singletons** on
`WorkflowState`. Multiple stories are only possible by calling `Runner.start_run()` N times with a
different `ticket_id` each — N independent runs, N run pages, no in-run fan-out.

**Locked decisions (2026-06-17, via client Q&A):**

| Decision | Choice |
|---|---|
| Fan-out model | **Per-story subgraph keyed by `ticket_id`, inside one run** (one checkpoint, one run page, one SSE stream). |
| Concurrency | **Sequential, one story at a time**, in dependency order. (Bounded parallelism is a later option.) |
| PR identity / no-dup | **Persist `branch` + `pr_url` per `ticket_id`**; Coding/Fixer update the existing PR (force-push + edit) instead of opening a new one. Deterministic branch name per story. |
| UI placement | **Run-new form toggle** (single/multiple) + **per-story controls on run detail** (Retry / Re-research / Regenerate PR / Re-review / Re-fix). |

---

## 2. State model — `WorkflowState` gains a per-story map

`src/ash/graph/state.py`.

```python
from typing import Annotated

StoryStep = Literal["research", "coding", "reviewer", "fixer"]

class StoryState(BaseModel):
    ticket_id: str
    title: str = ""
    deps: list[str] = Field(default_factory=list)        # ticket ids this story depends on
    status: Literal["pending","running","completed","failed","skipped"] = "pending"
    # deterministic identity → guarantees no duplicate PR on retry/regenerate
    branch: str | None = None
    pr_url: str | None = None
    failed_step: StoryStep | None = None                 # which sub-step errored (per-story retry)
    # the build-team namespaces, now PER STORY (same shapes as today)
    research: ResearchState = Field(default_factory=ResearchState)
    coding:   CodingState   = Field(default_factory=CodingState)
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    fixer:    FixerState    = Field(default_factory=FixerState)


def merge_stories(old: dict[str, StoryState], new: dict[str, StoryState]) -> dict[str, StoryState]:
    """Reducer: shallow-merge by ticket_id so a node that writes one story doesn't clobber others."""
    return {**(old or {}), **(new or {})}
```

`WorkflowState` additions:

```python
single_story: bool = True                      # from the form toggle; default single
stories: Annotated[dict[str, StoryState], merge_stories] = Field(default_factory=dict)
story_order: list[str] = Field(default_factory=list)   # dependency-sorted execution order
current_story: str = ""                        # the sequential cursor (ticket_id being built)
```

- The **flat** `research/coding/reviewer/fixer` namespaces on `WorkflowState` are **removed** as
  the source of truth for the build team; `intake`, `pm`, `rfc` stay flat (run-level).
- `brief()` already keys off `ticket_id`; the loop sets `ticket_id = current_story` each iteration,
  so per-story briefs work unchanged. `_ticket_brief` is reused.
- **`raw_to_dev`** (no spec) and **single-ticket** runs synthesize **one** story (id `"_main"` for
  raw_to_dev, or the chosen `ticket_id`) so there is exactly one code path.

> **Reducer note (risk):** `Annotated[..., reducer]` on a Pydantic `StateGraph` field is supported
> by LangGraph; validated in Phase F0's first test. If a nested-pydantic reducer misbehaves, fall
> back to nodes returning the **whole** `stories` dict (still correct, just less surgical).

---

## 3. Graph restructure — sequential per-story loop (LangGraph-idiomatic)

`src/ash/graph/builder.py`. The linear build chain is replaced by a **plan → loop → subgraph**:

```
START → intake → (route) → pm → pm_publish → rfc → plan_stories → story_router
                                                                      │   ▲
                                                          (next story)│   │ (story done → back to router)
                                                                      ▼   │
                                                              story_build  ─┘   (compiled SUBGRAPH:
                                                                                  research→coding→reviewer→fixer)
        story_router ──(no pending story)──► merge ──► END
```

- **`plan_stories`** (new node): after the spec is approved (or for raw_to_dev), build `stories`
  from `pm.spec.tickets` (or one synthetic story), topologically sort into `story_order`, assign a
  deterministic `branch` per story (`ash/<short-run-id>/<ticket_id>`). Single-story runs produce one
  entry. Idempotent: re-running it never discards an existing story's `pr_url`/`status`.
- **`story_router`** (conditional edge): pick the **next `pending` story whose deps are all
  `completed`** → set `current_story` + `ticket_id`, route to `story_build`. If none pending → route
  to `merge`. This is the "one by one" engine and is what makes retry "start from story 2" free:
  completed stories are skipped, the failed/pending one is picked up.
- **`story_build`** (compiled **subgraph**, no own checkpointer — parent's Postgres checkpointer
  persists it): `research → coding → reviewer → (fixer ⇄ reviewer, bounded) → done`. Mirrors the
  existing build edges. Subgraph interrupts (merge approval / manual trigger) **bubble** to the
  parent checkpointer (validated in the runtime plan P0); interrupt payloads now carry
  `ticket_id` so the UI knows which story is paused.
- **Node-adapter scoping (minimal agent churn):** agents keep `run(state) -> {"<ns>": {...}}`. A
  `scoped=True` flag on `make_node` (`graph/nodes.py`) (a) reads/writes the **current story's**
  namespace, (b) rewrites the agent's return into `{"stories": {current: {...}}}` through the
  reducer, and (c) sets the story's `status`/`failed_step`. Agents themselves barely change — they
  read `state.brief()` (already ticket-scoped) and their own sub-state via a helper
  `state.active(ns)` that resolves to `stories[current_story].<ns>`.

> **Alternative considered:** implement `story_build` as a plain node that calls the four agents
> in-process. Rejected — a real subgraph keeps interrupt/resume + checkpointing uniform and is the
> LangGraph-religious choice (CLAUDE.md rule).

---

## 4. PM — single vs multiple stories

### 4.1 The toggle (baseline, as asked)
- **State/Run:** new `story_mode: Literal["single","multiple"] = "single"` threaded through
  `Runner.start_run`, `POST /ui/runs`, `POST /runs`, persisted on `RunRecord` (PG backfill).
- **Form (`run_new.html`):** a toggle **"Stories: ● Single (default) ○ Multiple"** (Alpine).
- **PM prompt:** when `single`, instruct the PM to produce **exactly one implementation ticket**
  (a spike is still allowed if it must investigate first); when `multiple`, the current
  decomposition behavior. Reinforce with the deterministic `validate_spec` layer.

### 4.2 Better option (recommended) — decide *after* the PM sees the work
A pre-run binary toggle forces the decision before anyone knows how big the work is. Since PM
**already pauses at a HITL review gate** (`pm_publish` → `interrupt("spec_review")`), the more
informed UX is to **let PM always propose a decomposition, then let the human choose at the gate**
which tickets become stories:

- PM always emits its natural ticket breakdown.
- The spec-review gate (existing UI) gains a per-ticket **checkbox** ("build as a story") plus a
  **"Combine all into one story"** switch. The human's selection drives `plan_stories`.
- The form toggle becomes a **default/hint** (`single` pre-checks "combine"; `multiple` pre-checks
  all) — not a hard constraint. Best of both: sensible default, final say after seeing the spec.

This reuses the gate we already have (no new interrupt), keeps "mostly single" as the default, and
removes the guesswork. **Decision: implement 4.1 first (cheap), layer 4.2 onto the same gate in
F1.** `plan_stories` consumes the resolved selection regardless of which path set it.

---

## 4b. Structured LLM outputs — LangChain-first, Instructor as fallback

> Requirement: "use the LangChain ecosystem if it has something; otherwise Pydantic + Instructor."

**Current implementation (keep — it *is* the LangChain ecosystem option):**
- Every structured agent goes through `BaseAgent.build_agent()` → LangChain **`create_agent(...,
  response_format=<PydanticModel>)`** and/or **`model.with_structured_output(<PydanticModel>)`**.
  The schemas (`Spec`, `ImplementationPlan`, `CodeChange`, `CodeReview`, `RFCDocument`) are plain
  **Pydantic** models — LangChain binds them as the tool/JSON schema and returns validated objects.
- Because some gateways (Groq `gpt-oss-*` via LiteLLM) 400 when **tools + `response_format`** are
  sent together, agents run **two-phase**: explore (tools, no schema) → extract
  (`with_structured_output`, no tools). This is the documented Groq-safe path (parent plan §11).

**Decision:** structured output **stays on LangChain primitives** (`with_structured_output` /
`create_agent(response_format=...)`) — they already cover the need, support Pydantic schemas
natively, and keep one runtime (CLAUDE.md rule #6). **Instructor is a documented fallback only**,
introduced **iff** we hit a provider where LangChain's structured path is unreliable *and* the
two-phase extract doesn't rescue it. If added, Instructor would live behind the same
`BaseAgent._extract()` seam (swap the call, keep the Pydantic schema) so no agent code changes.
No new dependency now.

## 5. No-duplicate-PR — deterministic per-story identity

- **Branch:** `plan_stories` assigns `story.branch = f"ash/{run_id[:8]}/{ticket_id}"` once; never
  regenerated.
- **Coding:** before opening a PR, if `story.pr_url` is already set (retry/regenerate) → reuse the
  branch, **force-push**, and **edit** the existing PR (title/body) via `gh pr edit` instead of
  `create_pr`. Otherwise create once and record `branch`/`pr_url` on the story.
- **Fixer:** already updates the same PR; generalize to read `story.branch`/`story.pr_url`.
- **Persistence:** new lightweight **`StoryRecord`** table (`run_id`, `ticket_id`, `title`,
  `status`, `branch`, `pr_url`, `failed_step`, timestamps) so the runs-list/PR-dropdown and the
  no-dup check survive process restarts. **`AgentTask` gains a nullable `ticket_id`** so the
  per-agent task queue / dispatcher tracks work **per (agent, run, story)** (run-level for
  intake/pm/rfc). Both via the existing `_PG_COLUMN_BACKFILLS` stopgap until Alembic lands.

---

## 6. Retry on failure — per story, from the failed step

`src/ash/graph/runner.py`.

- `first_failed_step` → **`first_failed_story()`**: scan `stories` in `story_order` for the earliest
  with `status == "failed"`, return `(ticket_id, failed_step)`.
- `retry_run(run_id, *, ticket_id=None, from_step=None)`:
  1. Resolve target story (default: first failed) and step (default: its `failed_step`).
  2. Reset that story's namespaces from `from_step` onward, set `status="running"`,
     `failed_step=None`, `current_story=ticket_id`, run `status="running"`.
  3. `aupdate_state(as_node="story_router", ...)` so the router re-enters `story_build` for that
     story, then `ainvoke(None)`. Completed earlier stories are skipped by the router → **resume
     genuinely starts at story 2**; the failed story updates its existing PR (no dup).
- The "Run failed at story N / step X" banner + **Retry from here** button moves onto the per-story
  card (§8).

---

## 7. Manual per-story regeneration

New endpoints (web):

| Endpoint | Action |
|---|---|
| `POST /ui/runs/{id}/stories/{ticket_id}/rerun?step=research` | re-run Research for that story |
| `…/rerun?step=coding` | **regenerate the PR** (updates the same PR — no dup) |
| `…/rerun?step=reviewer` | re-review that story |
| `…/rerun?step=fixer` | re-run Fixer for that story |

Mechanism = the same `retry_run(run_id, ticket_id=…, from_step=step)` fork, but driven on demand
(not only on failure). Resets the story's namespaces from `step` onward, re-enters the router at
that story, and (for coding) updates the persisted PR. Each fires in the background and the run page
re-subscribes to SSE for live progress (mirrors today's `POST /ui/runs/{id}/retry`).

---

## 8. UI — per-story timeline, progress per PR, header links

`src/ash/web/templates/_run_timeline.html` + `run_status.html`.

- **Header (top-right):** keep the **source ↗** link; add a **PR link**. Single story → direct
  **View PR ↗**; multiple → a **PRs ▾** dropdown listing each story's PR with its status dot.
- **Run-level stages (top):** intake → PM → RFC stay as today.
- **Per-story section (replaces the single build pipeline):** one **story card** per `ticket_id`
  showing a mini 4-stage pipeline (Research → Coding → Reviewer → Fixer) with status dots/tokens,
  the story's PR link, and **per-story buttons**: Retry (if failed), Re-research, Regenerate PR,
  Re-review, Re-fix. Overall progress = **N/M stories done**.
- **Enable/disable already respected:** a disabled agent (`enabled=false`) or manual-trigger gate
  yields a per-story **skip/await** and the loop continues — verify the gate runs **per story** and
  never blocks the router.
- **SSE:** the existing `/ui/runs/{id}/events` stream re-renders `_run_timeline.html`; it now
  includes the per-story cards. Manual/merge interrupts surface on the **specific story** card
  (payload carries `ticket_id`).

---

## 9. RFC — one per run, Markdown preview, fix collapse

- **Always one RFC:** RFC is run-level (single `rfc` namespace) and runs once before
  `plan_stories` — already structurally true; add a guard so it is never story-scoped and never
  regenerated per story.
- **Markdown preview:** render `rfc.doc` as Markdown with a **Raw / Preview** toggle. Use a small
  client-side renderer (`marked` + `DOMPurify` via CDN) + Alpine toggle so switching is instant and
  needs no re-fetch. (Server-side `markdown` is the fallback if CDN policy disallows it.)
- **Fix auto-collapse:** the bug is that the SSE swap rebuilds the timeline and the RFC `<details>`
  isn't tracked by the open-state preservation script in `run_status.html` (unlike tickets/technical
  approach), so it resets to closed on the ~1.5 s tick. Fix = wrap the RFC block in **`hx-preserve`**
  (HTMX keeps the element across swaps) **or** give it a stable `data-key="rfc"` and add it to the
  preserve/restore `openDetails` set. `hx-preserve` is the cleaner fix.

---

## 10. Phasing (PR-sized; each keeps ruff + mypy --strict + pytest green)

| Phase | Scope | Status |
|---|---|---|
| **F0 — State + graph restructure** | `StoryState` + reducer; `plan_stories`, `story_router`, `story_build` subgraph; node-adapter scoping; `raw_to_dev`/single → one synthetic story. | ✅ shipped — single-story path behaviour-preserving; tests for reducer, router, scoping, topo-order. |
| **F1 — PM single/multiple + story selection** | `story_mode` through run/form/state/`RunRecord`; PM prompt branch (§4.1); post-PM per-ticket story selection at the review gate (§4.2; decision may be a dict `{action, stories}`) feeding `plan_stories`. | ✅ shipped — radio toggle on the form; multi-story checkbox selection at the gate; dependency `story_order`. |
| **F2 — Per-story PR identity** | deterministic branch (per ticket_id); Coding updates existing PR not create; `StoryRecord` + `AgentTask.ticket_id`. | ✅ shipped — branch/pr_url persisted per story; hydration backfills PR identity; Coding edits existing PR. |
| **F3 — Per-story retry** | `first_failed_story`, `retry_run(ticket_id, from_step)`, router resume; per-story failure banner. | ✅ shipped — failed story resets from its step and re-enters the router; completed stories skipped (test). |
| **F4 — Manual regenerate** | per-story rerun endpoint (`/ui/runs/{id}/stories/{ticket}/rerun?step=…`). | ✅ shipped — per-step ↻ + per-story Retry buttons; reuses `retry_run`; PR updated not duplicated (test). |
| **F5 — UI per-story timeline** | story cards + N/M progress + top-right PR link/dropdown + per-story buttons; SSE; per-story interrupt surfacing (`pending_story`). | ✅ shipped — per-story mini-pipelines, metric chips, review collapsibles, PR dropdown. |
| **F6 — RFC polish** | one-RFC guard (run-level node, never per story); Markdown preview (marked + DOMPurify); collapse fix. | ✅ shipped — RFC rendered as sanitised Markdown + raw fallback; re-render hook on SSE swaps. |
| **F7 — Context minimization** | chunked index with line ranges + line-range `read_file` + per-result snippet cap + story-scoped collection. (LangChain retriever/compression + summarization middleware + diff-mode `CodeChange` + AST chunking = follow-ups.) | ✅ core shipped — chunk-level Chroma index, `path:start-end` hits, `read_file(path, start, end)`. |
| **F8 — Agent analytics** | `AgentRunMetric` + capture in `make_node` + `db/metrics.py`; run-detail totals + per-story/stage chips; Agents-view rollups + Dashboard 7-day KPIs. | ✅ shipped — tokens (in/out) + time + model per agent/story, surfaced across run detail, Agents, Dashboard. |

> F0 is the keystone and the riskiest (state + graph). Land it behind a behaviour-preserving
> single-story path first, then F1 turns on real fan-out. **F7 (context) and F8 (analytics) are
> independent of the fan-out core** and can be scheduled in parallel — though F8 attributes metrics
> to a `ticket_id` best once F0 lands, and F7's wins are measured *by* F8.
> **Structured outputs:** no phase — keep LangChain `with_structured_output` / `response_format`
> (§4b); Instructor only if a provider forces it.

---

## 11. Risks & open questions

1. **Pydantic-state reducer** on `stories` — validate in F0; fallback = return whole dict.
2. **Subgraph interrupt bubbling** through the router loop — already validated (runtime plan P0);
   add `ticket_id` to interrupt payloads and to `get_run`'s overlay so the UI maps a pause to the
   right story.
3. **`get_run` overlay** currently assumes one interrupt; sequential execution means one active
   interrupt at a time → OK, but the overlay must report **which** `current_story` it belongs to.
4. **Dependency cycles** in `story_order` — reuse the spec validator's acyclic check; a cycle ⇒
   surface in `open_questions`, fall back to spec order.
5. **Worktree isolation + cleanup per story** — each story gets its own branch/worktree. Cleanup
   must run **per story**, triggered when that story reaches a terminal state
   (`completed`/`failed`/`skipped`) **and** on regenerate (remove the stale worktree before
   re-checkout), **not** once at `merge`. Today `_cleanup_worktree` runs only at `merge` over the
   single flat `research/coding` worktree path (`builder.py:29`). New design: `story_build` owns a
   `cleanup_story_worktree(state, ticket_id)` step that runs as the subgraph's exit, keeping the
   worktree alive across research→coding→reviewer→fixer for that story, then removing it. A
   safety-net sweep at `merge` removes any leftover per-run worktrees. See §12.5.
6. **Token/cost** — N stories = N build passes; keep per-agent `max_iterations` + per-ticket budget
   guard; surface per-story token totals on the card (now formalized as analytics, §13).
7. **Alembic** still stop-gapped; `StoryRecord`, `AgentTask.ticket_id`, and `AgentRunMetric` (§13)
   go through `_PG_COLUMN_BACKFILLS` / create-all until the migration phase.

---

## 12. Context minimization for Research → Fixer (send only what's needed)

> Requirement: "avoid sending unnecessary project code to the LLM; send only what is required."
> This section gives the **current final implementation** and a concrete **improvement plan**.

### 12.1 What is actually sent to the LLM today (current implementation)
The repo is **never bulk-uploaded**; agents pull context on demand through read-only tools inside a
bounded ReAct loop (`BaseAgent._explore`, `MAX_EXPLORE_STEPS=8`). The model sees: the brief, plus
the accumulated **tool outputs** appended as `ToolMessage`s each turn. The tools and their current
size caps:

| Stage | Toolkit | Tool | Returned to LLM (current cap) |
|---|---|---|---|
| Research | `CodebaseToolkit` (`toolkits/codebase.py`) | `search_codebase` | semantic hits, **up to 30**, each `doc[:800]` chars |
| | | `grep_code` | up to **60** `path:line:match` lines |
| | | `list_directory` | tree, up to **200** entries (`repo_tree`) |
| | | `read_file` | **6000** chars (truncated) |
| Coding / Fixer | `DevToolkit` (`toolkits/dev.py`) | `read_file` | **6000** chars |
| | | `list_files` | up to **80** glob matches |
| | | `search_code` | up to **40** hits (`code_intel.search`) |
| | | `run_command` | last **4000** chars of test/lint output |
| All | `WorkflowState.brief()` | — | spec/ticket brief, optional `max_chars` truncation (0 = no limit) |

**The two real waste sources (current implementation):**
1. **Whole-file granularity in the vector store.** `VectorStoreClient.index_directory`
   (`clients/chroma.py:59`) embeds **each entire file as one document** (`docs.append(text)`), and
   `search` returns `doc[:800]` of that whole-file document. So semantic retrieval is file-level,
   not chunk-level — poor precision, and the 800-char window is an arbitrary slice of a possibly
   huge file rather than the relevant span.
2. **Whole-repo indexing every run.** Research re-indexes the **entire** worktree per run
   (client-side embeddings in the `api` container — not LLM tokens, but time/compute) regardless of
   what the story touches.
   Plus: `read_file` always returns the **first 6000 chars** (no line-range targeting), so reading
   a large file to see one function ships the file head; and explore-loop `ToolMessage`s
   **accumulate** in the context with no compaction.

Net: LLM **input** cost grows with (a) imprecise retrieval slices and (b) accumulated tool chatter;
LLM **output** cost is dominated by `CodeChange` returning **full file contents** per edit (§12.4).

### 12.2 Improvement plan (LangChain-first)
Ordered by leverage; each is independently shippable and keeps the ReAct/tool model.

1. **Chunk-level indexing & retrieval (biggest win).** Replace whole-file documents with
   **chunked** documents using LangChain splitters —
   `RecursiveCharacterTextSplitter.from_language(...)` (Python/TS/Go/…) or a fixed token window with
   overlap. Store chunk metadata (`path`, `start_line`, `end_line`). `search_codebase` then returns
   the **relevant chunk(s) with line ranges**, not an 800-char slice of a whole file. Smaller, more
   relevant context per hit.
2. **Adopt LangChain retriever primitives** instead of the hand-rolled `VectorStoreClient.search`:
   wrap Chroma in `langchain_chroma.Chroma` + an embeddings object, expose a
   **`VectorStoreRetriever`**, and bind it via **`create_retriever_tool`**. Layer
   **`ContextualCompressionRetriever` + `EmbeddingsFilter`/`LLMChainExtractor`** so only the
   sentences/lines relevant to the query survive into context (drops boilerplate from each hit).
   This is the "send only what's required" lever, done with maintained LangChain components
   (CLAUDE.md rule #6).
3. **Line-range reads.** Extend `read_file(path, start_line=None, end_line=None)` (both toolkits +
   `code_intel.read_file`) so the agent reads a **span** (e.g. lines 120–180) rather than the first
   6000 chars. Search/retriever hits carry line numbers, so the agent can read exactly the hit.
4. **A per-step context budget.** Introduce a `context_budget` (chars/approx-tokens) per agent;
   rank tool results by relevance, **truncate + dedupe** to the budget before they enter the
   message list. Centralize in the toolkits so every tool respects one cap; tighten the current
   ad-hoc 800/6000/4000 numbers into one tunable policy.
5. **Compact the explore transcript.** Activate LangChain **`SummarizationMiddleware`** (already
   planned as runtime P6) on the explore loop so accumulated `ToolMessage`s are compressed once they
   exceed a threshold — keeps long investigations bounded.
6. **Scope indexing to the story.** Index **incrementally / on demand** rather than the whole repo
   each run: seed the index from the story's likely-relevant subtrees (PM `relevant_files`, the
   plan's `relevant_files`, paths named in the brief) and expand lazily; optionally persist the
   index keyed by commit SHA so re-runs don't re-embed. Cuts indexing time, not LLM tokens, but
   matters for "one by one" throughput.

### 12.3 (Stretch) AST-aware chunking
Later: tree-sitter / language-aware splitting so a "chunk" is a whole function/class with its
docstring — best retrieval precision. Deferred behind 12.1–12.2.

### 12.4 Output-side: diff-mode `CodeChange` (separate, high-value)
`CodeChange.FileEdit.content` currently carries the **full file** per edit — large **output** tokens,
and those full files re-enter context on every fix iteration. Option: support **unified-diff /
patch hunks** for `modify` actions (full content only for `create`), apply via `git apply`. Big
token saver on multi-iteration fix loops. Flag as its own decision (touches `apply_change`,
reviewer/fixer); not bundled into the retrieval work.

### 12.5 Worktree lifecycle (ties into the cleanup requirement)
Per-story worktree created at `story_build` entry, reused across research→coding→reviewer→fixer,
removed at the subgraph exit (terminal story state) and before any **regenerate** re-checkout.
`merge` keeps a best-effort sweep for leftovers. (See §11.5.)

### 12.6 Phasing
Land as **F7** after the fan-out core (F0–F5): F7a = chunked index + retriever tool + line-range
reads + context budget (12.1–12.4 retrieval items); F7b = summarization middleware + scoped
indexing; diff-mode `CodeChange` and AST chunking are separately-scheduled stretch items.

---

## 13. Agent analytics — tokens (in/out) & time, at the DB and on the UI

> Requirement: "add analytics — how many in/out tokens burned and time consumed by agents, at DB
> level and on the UI for corresponding agents."

### 13.1 What we already have
Every agent tracks usage via `BaseAgent._reset_usage()` / `_add_usage()` and returns
`tokens: {"prompt_tokens": N, "completion_tokens": N}` in its namespace; the run timeline shows
`↑prompt ↓completion` per stage. **Gaps:** not persisted beyond the checkpoint blob, **no timing**,
no per-run / per-story / per-agent / per-project aggregation, no model attribution, no cost.

### 13.2 DB level — new `AgentRunMetric` table
One row per agent execution (including each retry/regenerate, so history is complete):

```
AgentRunMetric:
  id, run_id, ticket_id (nullable → run-level for intake/pm/rfc),
  agent_name, model,
  prompt_tokens, completion_tokens, total_tokens,
  duration_ms, started_at, ended_at,
  status ("completed"|"failed"|"skipped"),
  attempt (1,2,… for retries), cost_usd (nullable; if model pricing is known)
```

**Capture point = the node adapter (`graph/nodes.py:make_node`)**, which already wraps every agent
run and sees the result dict: stamp `started_at` before `agent.run`, `ended_at` after, read
`result[ns]["tokens"]`, and best-effort insert a metric row (same pattern as the existing task
lifecycle — never blocks/crashes the run). For per-story agents, `ticket_id = current_story`.
`duration_ms` = wall-clock of the node. Cost = `tokens × per-model rate` from a small pricing map in
config (optional; blank when unknown).

> **LangChain-native token source:** prefer `UsageMetadataCallbackHandler` / response
> `usage_metadata` (already what `_add_usage` reads) so counts come from LangChain, not bespoke
> estimation.

### 13.3 Aggregation queries
`db/metrics.py`: totals + breakdowns by **run**, by **story**, by **agent**, by **project**, and
over a **time window** (e.g. last 7/30 days). Expose:
- per-run totals (sum tokens, sum duration) — for the run header strip,
- per-story per-agent (for story cards),
- per-agent rollups across runs (avg/median tokens + time) — for the Agents view,
- project/day rollups — for the Dashboard.

### 13.4 UI
- **Run detail:** a **totals strip** (Σ in/out tokens, Σ time, est. cost); each **story card** and
  each **stage** shows that agent's tokens + duration (extends today's `↑/↓` chips with a clock).
- **Agents view (`/ui/agents` + `/ui/agents/{name}`):** per-agent analytics card — avg/total
  tokens (in/out) and avg/total time across runs, plus a small sparkline/trend (HTMX-loaded
  fragment). This is the "for corresponding agents" surface.
- **Dashboard:** a KPI tile (tokens burned + time today / this week) and a top-N
  costliest-agents widget.
- All server-rendered (Jinja2 + HTMX), consistent with the current stack — no new frontend deps.

### 13.5 Phasing
**F8**: F8a = `AgentRunMetric` table + capture in `make_node` + `db/metrics.py` aggregations;
F8b = run-detail totals + story/stage chips; F8c = Agents-view analytics + Dashboard KPIs.
Independent of F0–F7 but most useful once per-story execution (F0) lands so metrics attribute to a
`ticket_id`.
