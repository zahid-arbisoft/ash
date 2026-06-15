# Onboard a project — setup runbook

The end-to-end "point ASH at a repo and run it" guide. If you only want the env-var reference, see
[configuration.md](configuration.md); to add an issue source connector, see
[integrations.md](integrations.md). This doc ties it all together.

> **TL;DR:** install → fill `.env` → start Postgres + Chroma → write `projects/<name>.yaml` →
> point it at a **local clone** → add the issue source in `/admin` → start a run at `/ui/runs/new`.
> **A fork is *not* required** — see [§3](#3-repo-topology-do-i-need-a-fork).

---

## 0. Prerequisites

- **Python ≥ 3.12** (3.13 works too) and **Docker** (for Postgres + Chroma).
- A clone of the repo you want ASH to work on, somewhere on disk.
- An **LLM**: either an Anthropic API key, or any OpenAI-compatible gateway (LiteLLM / Ollama /oMLX/
  vLLM) URL + key.
- A **GitHub token** with access to the *work* repo (needed to push branches and open/merge PRs;
  also needed to read issues from a private source repo).

---

## 1. Install & infra

```bash
just setup          # creates .venv (py>=3.12), editable install + dev tools
cp .env.example .env
just db-up          # Postgres (docker compose)
just chroma-up      # Chroma vector store (semantic code search for the Research agent)
```

`gh` is the recommended way to wire git auth for headless pushes:

```bash
gh auth login
gh auth setup-git   # ASH pushes over HTTPS using these creds (not the clone's SSH origin)
```

---

## 2. Fill in `.env`

Open `.env` and set (full reference: [configuration.md](configuration.md)):

| Variable | What to set |
|---|---|
| `LLM_PROVIDER` / `LLM_MODEL` | `anthropic` + `claude-sonnet-4-6` (or `openai` + your gateway model) |
| `ANTHROPIC_API_KEY` *or* `OPENAI_API_KEY` (+ `LLM_BASE_URL`) | the key for the provider you chose |
| `POSTGRES_DSN` | leave the default if you used `just db-up` |
| `SECRET_KEY` | **generate one** — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` (encrypts connector tokens at rest) |
| `ADMIN_USER` / `ADMIN_PASSWORD` | the `/admin` login (change `change-me`) |
| `GITHUB_TOKEN` | a PAT with access to the work repo (push + PR scope; `repo` for private) |
| `CHROMA_HOST` / `CHROMA_PORT` | leave defaults if you used `just chroma-up` |

Per-agent model overrides are optional: `AGENT_PM__MODEL`, `AGENT_CODING__MODEL`, etc.

Create the first admin login (password is hashed, never stored in plaintext):

```bash
just create-admin <username>     # prompts for the password
```

---

## 3. Repo topology — do I need a fork?

**No.** Topology is per-project via `work.mode`:

| `work.mode` | When to use it | Issues come from | Code is pushed to |
|---|---|---|---|
| **`single`** | **Your org's own repos** (the common case) | the repo itself | the same repo |
| **`fork`** | A repo you *don't own* (open-source upstream) | upstream (read-only) | your fork |
| closed-source | a private `single` (or `fork`) repo | — | — (just an auth/scope detail) |

- For an **internal project**, use `mode: single` with `source_repo == target_repo` and a token that
  can write to it. No fork, no upstream.
- Use `mode: fork` only when ASH must read issues from a repo you can't push to (e.g. the
  `edx-platform` / `plane` examples) and deliver PRs to your fork instead.

---

## 4. Write `projects/<name>.yaml`

Copy [`projects/plane.yaml`](../projects/plane.yaml) and edit. Two shapes:

**A) Internal repo (`single`) — most projects:**

```yaml
name: my-service
issues:
  source_repo: my-org/my-service      # read issues here
  filters: { state: open }
work:
  target_repo: my-org/my-service      # SAME repo — no fork
  base_branch: main
  mode: single
  local_repo_path: /abs/path/to/clone/my-service   # ← enables Research + Coding
autonomy:
  require_human_for_merge: true       # flip to false for unattended merges
  require_human_for_escalation: true
budget: { per_ticket_usd: 2.0, per_day_usd: 20.0 }
skills:                               # optional: skills/<name>/SKILL.md
```

**B) Fork of an upstream you don't own:**

```yaml
name: edx-platform
issues:
  source_repo: openedx/edx-platform   # read-only upstream
work:
  target_repo: my-org/edx-platform    # your fork — branches/PRs/merges go here
  base_branch: master
  mode: fork
  upstream_remote: openedx/edx-platform
  open_upstream_prs: false
  local_repo_path: /abs/path/to/clone/edx-platform
autonomy: { require_human_for_merge: true, require_human_for_escalation: true }
```

**Key field: `work.local_repo_path`** — the absolute path to a local clone of the *work* repo.
Without it (and without `LOCAL_REPO_PATH` in `.env`), Research/Coding **skip gracefully** and you get
a PM-only run. Each ticket builds in its own **git worktree** off this clone, so the clone is never
mutated directly.

> **Running in Docker?** Set `LOCAL_REPOS_ROOT` in `.env` to the common parent of all your clones
> (e.g. `/Users/you/dev`) so the container can resolve every project's `local_repo_path`.

---

## 5. Add the issue source connector

Start the app and add the source in the admin portal (token encrypted at rest):

```bash
just serve        # http://127.0.0.1:8000  (UI /, admin /admin, API /docs)
```

- Go to **`/admin` → Connectors → Create**: pick the kind (github/jira/plane), paste the token in
  **Secret**, set **Config** (e.g. `{"repo": "my-org/my-service"}`), check **Is source** + **Enabled**.
- A connector can be **source**, **sink** (where PM pushes tickets), or both. Pick a default sink, or
  leave it and PM writes a local file board under `runtime/<project>/`.
- Full per-kind field reference is on **`/ui/connectors`**.

(You can skip connectors entirely and do an **attachments-only** run — upload a spec file and leave
the item id blank.)

---

## 6. Start a run

UI: **`/ui/runs/new`** — pick project, source connector, item id (issue number / Jira key), the
**intake mode**, and **single vs multiple stories**:

- **Single story (default)** — PM produces one story; the build team ships one PR.
- **Multiple stories** — PM decomposes the work; each story is built **one by one** into its own PR
  (you choose which stories at the review gate).

Intake modes: `raw_to_spec` (PM writes the spec), `spec_ready` (PM extracts tickets from a provided
spec), `raw_to_dev` (skip PM, build straight from the issue).

Or via the API:

```bash
curl -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"project":"my-service","item_id":"123","integration_id":1,"intake_mode":"raw_to_spec","story_mode":"single"}'
curl localhost:8000/runs/<run_id>      # status + per-story state
```

Watch progress on the run page: PM → (RFC) → then one card per story (Research → Dev → Reviewer →
Fixer) with live status, per-step tokens/time, and the PR link(s) top-right. Approvals (spec review,
merge) appear inline and in **`/ui/approvals`**.

---

## 7. Verify it's working

```bash
just check          # ruff + mypy --strict + pytest (what CI runs)
just list <project> # list open issues from the source repo (sanity-checks the connector/token)
```

A healthy run logs (for the Research agent): worktree created → chroma indexed (or skipped, see
below) → LLM plan loop → Coding → PR opened.

---

## 8. Troubleshooting

**"Research is RUNNING but nothing is happening / no LLM call yet."**
Before its first LLM call the Research agent (a) creates a git worktree and (b) indexes the worktree
into Chroma for semantic search — embeddings run **locally** in the `api` container. On a **large
repo** this indexing can take minutes. ASH now guards against this: above `INDEX_MAX_FILES`
(default **1500**) it **skips semantic indexing and uses grep-based search** instead, and logs
progress every `INDEX_PROGRESS_EVERY` files while indexing. To always index regardless of size, set
`INDEX_MAX_FILES=0`. To force grep-only, set it to `1`.

**Research/Coding "skipped: no local clone available."**
Set `work.local_repo_path` (absolute) in the project YAML, or `LOCAL_REPO_PATH` in `.env`. In Docker,
also set `LOCAL_REPOS_ROOT` so the path resolves inside the container.

**LLM step errors / 400s.** See the checklist at the bottom of [configuration.md](configuration.md).
`LLM_MODEL` must be a model your key may use; a LiteLLM gateway uses `LLM_PROVIDER=openai` +
`LLM_BASE_URL=…/v1` (not `anthropic`).

**Push/PR fails.** Run `gh auth setup-git`; ensure `GITHUB_TOKEN` can write to `work.target_repo`.

**Small-context / local models hang or overflow.** Lower `EXPLORE_STEPS`, `EXPLORE_TOOL_CHARS`,
`EXPLORE_WINDOW`, `BRIEF_MAX_CHARS` (examples in `.env.example` / configuration.md).

---

## 9. Onboarding a second project

Add `projects/<name2>.yaml` (+ optional `skills/<name2>/SKILL.md`), add its connector in `/admin`,
and start a run. Nothing in `src/` changes — the engine is generic, projects are data.
