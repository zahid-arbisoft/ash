# Proposal: UI & Flow Overhaul — Run Cockpit, Fully-Manual Agents, Per-Agent HITL

## Why

The UI has grown organically into a sprawl of overlapping pages (runs list, run
timeline, PM workbench + its two list pages, Dev workbench + its list page, agents,
approvals) that each re-implement run status, story cards, and controls. This causes:

- **No single home for a run.** A run's work is scattered across `/ui/runs/{id}`,
  `/ui/pm/{id}`, and `/ui/dev/{id}` with inconsistent controls and duplicated story-card
  templates. The client cannot see the whole pipeline for one run in one place.
- **Inconsistent control surface.** Stop/restart/retry/trigger/HITL-feedback exist for
  PM and Dev but not uniformly for Research/Reviewer/Fixer/RFC, and the buttons differ
  per page.
- **Trigger model is implicit.** Manual is already the per-agent default for everything
  except PM, but the UI never presents a clean "fire this agent" cockpit, so the client
  can't drive a run agent-by-agent.
- **No custom prompts.** There is no way to add free-text instructions when starting a
  run or when re-triggering an agent for a better result.
- **Weak observability for Dev.** The `AgentLLMExchange` table already has `context` and
  `code` columns, but the Dev (coding) agent never populates them — so the client cannot
  see *which code was sent to the LLM* or *its token size*, which is the single most
  important cost signal for a coding agent.
- **The new-run form is overloaded** (8 fields, mixed source/destination/mode concerns)
  and the I/O page is a flat dump that's hard to analyze.
- **"coding" vs "Dev" naming** is inconsistent and confusing for clients.

## What changes

This is a UI/flow overhaul plus targeted backend gap-filling. ~70% UI, ~30% backend.

1. **Run cockpit (new primary view).** One page per run (`/ui/run/{run_id}`) built around a
   **pipeline rail** (Intake → PM → RFC → Research → Dev → Reviewer → Fixer; build stages
   are per-story) with live status + token chips on each stage. Clicking a stage opens that
   agent's **workbench panel** below. Each per-agent panel is deep-linkable
   (`/ui/run/{run_id}/{stage}`), satisfying "separate pages per agent, linked by run id."

2. **Fully-manual agents by default.** Every agent (PM included) defaults to
   `trigger=manual`. A new run sits idle at the first gate; the client fires each agent
   from the cockpit. Auto remains a per-agent opt-in (DB/YAML policy override).

3. **Uniform per-agent control surface** on every stage panel: **Trigger**, **Stop**,
   **Restart**, **Re-trigger (with optional custom prompt)**, and **HITL feedback** — at
   both the agent level and the per-story level (generalizing the existing PM/Dev pattern
   to Research, Reviewer, Fixer, RFC).

4. **Custom prompts.** Optional free-text instructions (a) when starting a run, and (b)
   when re-triggering any agent. Threaded into the agent's prompt for that pass, then
   consumed once.

5. **Dev code-to-LLM observability.** The Dev/Research/Fixer agents populate the
   `code` and `context` columns: which files/snippets were sent to the model and their
   token size. Surfaced on the I/O page with a per-exchange token breakdown
   (prompt = context + code + instructions).

6. **Rename `coding` → `Dev` everywhere** (agent name, labels, routes, templates), with a
   read-time alias so historical `agent_name="coding"` rows still display.

7. **Redesigned new-run form** split into **Source** and **Destination & options**
   sections (advanced fields collapsed), with a custom-prompt field.

8. **Dedicated I/O page** (`/ui/io`) across all runs — filter by run/agent/story/phase,
   token analytics, improved per-exchange viewer (sent/received, context, code with token
   size). Per-run and per-agent I/O remain reachable from the cockpit.

9. **Redesigned dashboard + run hub list** replacing the old runs timeline page and runs
   table. The dashboard gains more live status (active runs, gates awaiting action,
   token/time KPIs); the run hub list is a richer, cleaner index.

10. **Removed:** the old run timeline page (`/ui/runs/{id}` timeline view), the old runs
    table (`/ui/runs`), and the standalone PM/Dev workbench *list* pages — their function
    is absorbed by the run hub list + cockpit. (Per-agent workbench *panels* live on in
    the cockpit.)

## Impact

- **Affected specs:** `web-ui` (new), `hitl` (manual default + per-agent feedback),
  `agent-loop` (custom prompts, code capture), `coding-agent` → renamed behavior to
  `dev-agent`, `api` (route changes).
- **Affected code:** `src/ash/web/` (routes + templates, largest), `src/ash/config/settings.py`
  (`DEFAULT_AUTO_TRIGGER_AGENTS` → empty), `src/ash/graph/state.py` (custom-prompt +
  feedback fields), `src/ash/graph/runner.py` (generalized refine/re-trigger),
  `src/ash/agents/` (coding→dev rename, code capture), `src/ash/db/` (exchange queries,
  agent-name alias).
- **Backward-incompatible UI routes** (old `/ui/runs/{id}`, `/ui/runs`, `/ui/pm-workbench`,
  `/ui/dev-workbench`) are removed or redirected. No external API (`/runs`) changes.
- **No DB migration required** for the rename (read-time alias); custom-prompt/feedback
  fields live in checkpointed graph state, not new tables.

## Out of scope

- A full draggable node canvas (n8n/Langflow-style) — deferred; the pipeline rail gives
  the visual-flow feel without a canvas engine.
- Real-time collaborative editing, multi-user presence.
- Changing the external `POST /runs` API contract.
