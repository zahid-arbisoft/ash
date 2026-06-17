# Plan — Deployment topology, code-extraction quality & Deep Agents analysis

> **Status:** DISCUSSION / ARCHITECTURAL DECISION — no code yet (2026-06-18).  
> Decisions locked: **#28 (Topology B1)**.  
> Next concrete step: retrieval substrate (tree-sitter repo map) → Deep Agents inner loop (in that order).

---

## 0. Background — the trigger question

The session that produced this analysis started with the question:

> *"If I deploy it on AWS, how do I provide access to repositories like edx-platform? Code resides
> on local machine or GitHub. Do you understand my requirement and concern?"*

The core tension: ASH's Research and Coding agents need **direct filesystem access** to the repository
(worktrees, file reads, Chroma indexing). Hosting ASH on AWS means the agents run remotely — but a
50k-file monorepo like `edx-platform` can't be pulled to cloud on every run without cost, latency, and
data-residency problems.

---

## 1. Deployment topology analysis

Three topologies were evaluated.

### Topology A — Cloud-native (code pulled to cloud)

```
  Developer laptop
  (code at rest)
        │
        │  git clone / S3 sync
        ▼
  ┌─────────────────────────────────────┐
  │  AWS ECS / Lambda / EC2             │
  │  ASH engine + agents + worktrees    │
  │  Chroma (in-container)              │
  │  Postgres (RDS)                     │
  └─────────────────────────────────────┘
        │ API calls (snippets)
        ▼
  Claude / OpenAI / LiteLLM
```

**What breaks on edx-platform:**

| Problem | Detail |
|---------|--------|
| Cost | `git clone openedx/edx-platform` → ~300 MB, 50k+ files. Pulling every run or even per-run-caching on EBS is expensive and slow. |
| Latency | Clone + `chroma.index_directory` (local ONNX embeddings) already blocks for minutes on a local machine with the 1500-file cap. On a 1-vCPU container it's worse. |
| Data residency | Source code leaves the on-prem boundary — a hard blocker for regulated orgs, internal tooling, and any repo with undisclosed proprietary code. |
| Worktree disk | Each story needs a git worktree (~repo size on disk). Multiple parallel stories × large repo = EBS cost. |

**Verdict: unsuitable for large repos or any org with data-residency requirements.**

---

### Topology B1 — Hybrid: cloud control plane + local runner daemon (CHOSEN)

```
  ┌─────────────────────────────────────────────────────────┐
  │  On-premises / developer machine                        │
  │                                                         │
  │  ┌─────────────────────────────────┐                    │
  │  │  Local clone                    │  (code never       │
  │  │  edx-platform / plane / ...     │   leaves this box) │
  │  └─────────────────────────────────┘                    │
  │           │                                             │
  │  ┌────────▼────────────────────────┐                    │
  │  │  ASH Runner Daemon              │                    │
  │  │  - worktree create/cleanup      │                    │
  │  │  - Chroma index (local ONNX)    │                    │
  │  │  - DevToolkit / CodebaseToolkit │                    │
  │  │  - git push (via gh HTTPS)      │                    │
  │  └────────┬─────────────┬──────────┘                    │
  │           │             │ file-system tool calls        │
  │           │             ▼                               │
  └───────────┼──── local repo ──────────────────────────── ┘
              │
              │  WebSocket / long-poll dial-out
              │  (runner initiates; no inbound ports needed)
              ▼
  ┌──────────────────────────────────────────────────────────┐
  │  AWS / Cloud                                             │
  │                                                         │
  │  ┌──────────────────────────────────┐                   │
  │  │  ASH Control Plane               │                   │
  │  │  FastAPI + Postgres checkpointer │                   │
  │  │  LangGraph orchestration         │                   │
  │  │  Admin/UI + Approvals            │                   │
  │  └──────────────────────────────────┘                   │
  │           │                                             │
  │           │  API calls (only code *snippets* go here)  │
  │           ▼                                             │
  │  Claude / OpenAI / LiteLLM                              │
  │  (LLM vendor)                                           │
  └──────────────────────────────────────────────────────────┘
```

**How it works:**

1. Control plane owns: run lifecycle, LangGraph checkpointing, PM/RFC agents (no code needed), UI,
   approvals, ticket push to Jira/Plane.
2. Runner daemon owns: filesystem access, worktrees, Chroma indexing, git push.
3. Communication: the runner **dials out** to the control plane (no inbound AWS rules), polling
   or holding a WebSocket. The control plane dispatches graph steps that require filesystem access;
   the runner executes them locally and returns structured results (code snippets, search hits, file
   content).
4. Only **extracted code snippets** ever leave the on-prem boundary — not whole files, not the full
   repo. The LLM receives only what the agent extracted.

**Properties:**

| Concern | B1 answer |
|---------|-----------|
| Code stays on-prem? | **Yes** — only snippets go to LLM vendor. |
| Works on any repo size? | **Yes** — clones are local, `INDEX_MAX_FILES` cap prevents local hangs. |
| Data-residency compliance? | **Yes** — code never reaches AWS storage. |
| Inbound firewall rules? | **None needed** — runner dials out. |
| Cloud cost | Low — control plane is stateless light compute; no large EBS volumes. |
| Complexity | Medium — need a "runner agent" seam + dial-out protocol. |

**This is the chosen topology (decision #28).** All future B1 implementation work is gated on the
core graph loop being trustworthy (current status) and Alembic migrations landing first.

---

### Topology B2 — Hybrid: cloud control plane + GitHub Actions runner

Same split as B1 but the runner is a **self-hosted GitHub Actions runner** on the on-prem machine.
The control plane dispatches `workflow_dispatch` events; the runner picks up jobs.

**Why B1 is preferred over B2:**

- GitHub Actions is designed for CI, not for long-lived interactive agent loops (10 min job limit,
  no bidirectional comms mid-job, complex artifact passing).
- B1 gives full control over the runner protocol and can support mid-run interrupts and resume
  (LangGraph checkpoints) naturally. B2 would need awkward workarounds for HITL gates.
- B1 is also compatible with `aider` or `claude` as a **drop-in coding engine** (see §3).

---

## 2. Code-extraction quality analysis

During the same session the grounding quality of the Research and Coding agents was assessed against
a reference baseline (Claude Code = 100).

**Current ASH score: ~38 / 100**

### Score breakdown

| Dimension | ASH today | Explanation |
|-----------|-----------|-------------|
| **Tool set** | 75 | `list_files`, `grep_code`, `read_file`, `run_command`, `search_code` — strong basic toolkit; comparable to Claude Code. |
| **Loop quality** | 35 | Two-phase explore→extract with an 8-step sliding window. Doesn't maintain a structured plan/todo across steps; doesn't spawn sub-agents to quarantine heavy exploration context. |
| **Structural map (repo map)** | 5 | **The biggest gap.** No tree-sitter, no call graph, no import graph. The agent must greedily navigate a 50k-file tree by `grep_code` guesses rather than reading a pre-built structural skeleton. This alone is responsible for most hallucinated paths in generated PRs. |
| **Context compaction** | 45 | F7 added chunk-level indexing + line-range reads. Still accumulates full tool-call chatter in the context window; no `ContextualCompressionRetriever` or summarization middleware yet. |
| **Symbol precision** | 30 | Chroma search returns `path:start-end: snippet` chunks but there is no symbol-level index. A grep for `def authenticate` across a 50k-file repo returns dozens of unranked hits; the agent takes the first one. No PageRank-style importance weighting. |
| **Retrieval quality** | 40 | Chroma cosine-only; no hybrid sparse+dense (BM25 + cosine); no reranker (cross-encoder). Relevant chunks in different phrasing or with low embedding overlap get missed. |

### Improvement path (phased)

The phases are listed in order of impact per engineering effort.

#### Phase R1 — Tree-sitter repo map (biggest single gain, ~+17 pts)

Build a **structural skeleton** of the repo:
- Parse every file with `tree-sitter` (Python/JS/TS/Go/Rust bindings) → extract:
  - Classes, functions, methods with their file+line range.
  - Import graph (who imports what).
  - Call graph (who calls what) via AST-level name resolution.
- Rank entries by **PageRank on the import/call graph** so the most-imported utilities rank high.
- Produce a compact text representation (signtaures only, no bodies) that fits in ~2k tokens.
- Feed this "repo map" as a system-prompt prefix to the Research agent's explore loop.
- **Effect:** the agent no longer gropes in the dark — it starts with a map of every entry point and
  their relationships, directly answering "which module owns this?" before writing a single grep.

**Estimated score after R1: ~55 / 100.**

#### Phase R2 — Hybrid retrieval (BM25 + Chroma cosine + rerank, ~+8 pts)

Replace the current Chroma-only retrieval with a hybrid retrieval pipeline using LangChain's
`EnsembleRetriever`:

```
query ─► BM25 (sparse, exact keyword)   ─┐
       ► Chroma cosine (dense, semantic) ─┴► EnsembleRetriever → cross-encoder rerank → top-k
```

- **BM25** catches exact identifiers (`authenticate`, `UserSerializer`) that embeddings often miss.
- **Cross-encoder reranker** (e.g. `cross-encoder/ms-marco-MiniLM`) rescores the fused candidate
  list with a proper relevance model rather than embedding distance.
- Directly replaces `VectorStoreClient.search()` behind the existing tool interface.

**Estimated score after R1+R2: ~63 / 100.**

#### Phase R3 — Symbol index (precise go-to-definition, ~+7 pts)

A lightweight **symbol-to-location** index built alongside the repo map (tree-sitter already has
all the data). Exposes a `find_symbol(name, kind)` tool to agents:
- "Where is `AuthenticationMiddleware` defined?" → exact file + line, not a grep-over-the-whole-repo.
- Used by the Coding agent to jump directly to the file it needs to edit.

**Estimated score after R1+R2+R3: ~70 / 100.**

#### Phase R4 — Context budget + summarization middleware (~+8 pts)

Add `SummarizationMiddleware` (LangChain `ContextualCompressionRetriever` pattern):
- Before feeding retrieved chunks to the LLM, summarize each to `N` tokens (preserving the
  file+line attribution).
- Apply a **context budget** (e.g. max 12k tokens for retrieved context); drop lower-ranked chunks
  when over budget.
- Add a story-scoped **diff-mode `CodeChange`**: instead of sending full file content, send only
  the relevant hunk (±20 lines around the target symbol).

**Estimated score after all R-phases: ~78 / 100.**

Closing the gap to ~85+ requires a proper **agentic scratchpad** (persistent notes the agent
accumulates without re-reading), which is what Deep Agents (§3) provides — it's the multiplier on
top of the retrieval substrate.

---

## 3. Deep Agents — where it fits

### What it is

`deepagents` is LangChain's library packaging of the "deep agent" pattern (Claude Code / Deep Research
shape), built **natively on LangGraph**. It provides four components:

| Component | Purpose |
|-----------|---------|
| `write_todos` (planning tool) | Agent maintains an explicit, visible task list — stays coherent over long multi-step work without repeating steps. |
| Sub-agent spawning | Heavy exploration delegated to a child agent in its own context window; parent receives only a distilled summary. Keeps the main coding context lean. |
| Virtual filesystem as scratch memory | `ls`/`read_file`/`write_file`/`edit_file` tools let the agent offload findings to "files" instead of carrying them in the prompt. Backed by in-state mock FS (swappable). |
| `create_deep_agent` | Returns a compiled LangGraph that can be nested like any other subgraph. |

It is an **agent loop/orchestration library**, not a retrieval system. It improves *how the agent
navigates*; it does not tell it *where to look*.

### Where it fits in ASH

**Target: inner-loop engine for Research and Coding nodes (micro level only).**

```
  ASH macro graph (builder.py)
  ─────────────────────────────────────────────────────
  intake → pm → pm_publish → rfc → plan_stories
    → story_router → story_build subgraph
        ├── research node     ◄── deep_agent engine (Research explores/plans)
        ├── coding node       ◄── deep_agent engine (Coding writes/tests)
        ├── reviewer node
        ├── fixer node
        └── story_finalize
  ─────────────────────────────────────────────────────
  (deep_agent is nested INSIDE one node, NOT at macro level)
```

Do **not** use `deepagents` at the macro level — the ASH LangGraph already owns PM → stories →
build → review. `create_deep_agent` is called from within one graph node and its results (plan /
code changes) are folded back into `StoryState` by the existing hydrate/fold adapter.

### Effect on the quality score

| What you add | Score |
|---|---|
| ASH today (baseline) | ~38 |
| + deepagents inner loop (better loop quality + sub-agent context quarantine) | ~50 |
| + retrieval substrate (R1–R4: repo map + hybrid retrieval + symbol index) | ~70–78 |
| + deepagents driving the substrate tools | ~80–85 |

The retrieval substrate is the dominant factor. Deep Agents is the **multiplier** on top.

### Structured output compatibility note

Deep agents are tuned for open-ended tool loops, not constrained schema returns (`CodeChange` /
`ImplementationPlan`). The recovery pattern:

1. Let the deep agent loop run open-endedly.
2. At the end, read the agent's virtual-filesystem scratch files as "notes".
3. Run one final `with_structured_output` call (the existing `_extract` phase in `BaseAgent`)
   feeding the notes as context → produces the typed schema.

This means the existing two-phase `_explore` → `_extract` split in `BaseAgent` remains valid;
`deepagents` replaces the `_explore` step, `_extract` stays.

### Implementation sequencing

1. **Build the retrieval substrate first** (R1: tree-sitter repo map — highest ROI, engine-agnostic).
2. **Then** swap `BaseAgent._explore` for a `create_deep_agent`-based loop that drives those tools,
   with a retrieval sub-agent for context isolation.
3. Keep it behind the **pluggable engine seam** (§4) so `aider`/`claude` remain swap-in alternatives
   and `deepagents` API churn never holds the rest of ASH hostage.

### Caveats

- **API is young.** Pin the version. Expect churn; don't let it block unrelated work.
- **No lock-in.** `deepagents` is itself LangGraph — it nests cleanly with zero new primitives.
- **B1 synergy.** On the local runner the deep agent's virtual filesystem can be backed by the **real
  worktree filesystem** (the runner already has disk access), making the in-state mock redundant and
  giving the agent a fully persistent scratchpad across LangGraph checkpoints.

---

## 4. Pluggable coding engine seam (B1 runner design)

The B1 runner introduces a natural seam where the **coding engine** can be swapped per project.

```yaml
# projects/edx-platform.yaml
work:
  local_repo_path: /Users/zahid/dev/edx-platform
  coding_engine: ash          # ash | aider | claude | deepagents
```

| Engine | What it is | Trade-off |
|--------|-----------|-----------|
| `ash` (default) | Current `BaseAgent` two-phase loop + DevToolkit | Full control, LangChain-native. Grounding is shallow today (improving via R-phases). |
| `deepagents` | `create_deep_agent`-based loop driving ASH tools | LangChain-native upgrade; max control; you supply the retrieval tools. Best path for ASH-native improvement. |
| `aider` | OSS CLI; state-of-the-art repo-map (tree-sitter); rich diff mode | Mature retrieval; less control; structured output needs wiring; external process dependency. |
| `claude` | Claude Code CLI invoked as subprocess | The reference 100/100 baseline; not OSS; model-locked; no structured output; $$ per invocation. |

The `deepagents` option is the **LangChain-pure, low-lock-in path** — it honours the "LangGraph-first"
non-negotiable (CLAUDE.md §6) and keeps full control over tools, models, and structured outputs.
`aider` is the "borrow their tree-sitter" option if building R1 in-house is too slow. `claude` is a
manual-only escape hatch.

The seam is a thin adapter: `CodingEngineRunner.run(story_state, project) → StoryState`. The
current Coding agent is `engine="ash"`. Any engine writes its results to the same `StoryState` fields
(`branch`, `pr_url`, `note`, `error`).

---

## 5. Open questions / next steps

| # | Item | Priority |
|---|------|----------|
| 1 | **B1 runner daemon design** — WebSocket vs long-poll vs gRPC; auth (mTLS? shared secret?); how LangGraph interrupts bubble through the runner seam | Medium (post-Alembic) |
| 2 | **R1 tree-sitter repo map** — choose Python binding (`tree-sitter` PyPI), languages to support, map format (compact text vs embedded JSON), integration into `VectorStoreClient` | High (biggest retrieval gain) |
| 3 | **R2 hybrid retrieval** — `langchain-community` `BM25Retriever` + `EnsembleRetriever`; cross-encoder reranker model selection | Medium (after R1) |
| 4 | **R3 symbol index** — leverage tree-sitter already built for R1; expose as `find_symbol` tool | Low (after R2) |
| 5 | **deepagents inner loop** — `create_deep_agent` wrapping `DevToolkit`/`CodebaseToolkit`; virtual-FS backed by real worktree on B1 runner; structured-output recovery via existing `_extract` | Medium (after R1) |
| 6 | **Alembic migrations** — replace `_PG_COLUMN_BACKFILLS` stopgap before B1 runner work starts | High (blocker for B1) |

---

## 6. Decision record

**Decision #28 — Deployment topology = B1 (cloud control plane + local runner daemon)**

- **Context:** ASH agents need filesystem access to large repos (edx-platform, plane). Cloud-native
  (pull repo to AWS) has cost, latency, and data-residency problems.
- **Decision:** Topology B1. Cloud runs: FastAPI control plane + LangGraph + Postgres + UI/Approvals +
  PM/RFC agents. On-prem runs: runner daemon with filesystem access, worktrees, Chroma, git push.
  Only code snippets are extracted and sent to the LLM vendor; the full repo never leaves the
  on-prem boundary.
- **Why not B2 (GitHub Actions):** designed for CI, not interactive agent loops — no mid-run HITL,
  10 min job limit, no bidirectional comms.
- **Implementation gate:** Alembic migrations must land first (removes `_PG_COLUMN_BACKFILLS`
  stopgap). Runner seam design follows.
- **Date:** 2026-06-18.
