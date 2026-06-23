# Proposal: Workflow Builder — user-defined, versioned agent flows

## Why

Today the agent pipeline order and each agent's trigger mode (auto/manual) are fixed in code
(`intake → pm → rfc → research → dev → reviewer → fixer`) and configured per-project only via
`projects/<name>.yaml` or DB `AgentPolicyRecord`. The client cannot:

- Define **which agents run and in what order** for a given run (e.g. skip Research, jump PM → Dev,
  trigger RFC then stop), without editing YAML.
- Save **multiple named flows** and pick one per run.
- Drive **per-story** execution order (run agents for one story, or all stories one-by-one in a
  chosen order) from a reusable definition rather than ad-hoc clicks.

The cockpit already lets a client *manually* step a run agent-by-agent, but every run starts from
the same hard-coded shape and every agent defaults to manual. A **Workflow** makes the shape itself
data: a reusable, named, versioned definition of the agent flow and per-agent trigger policy,
selectable at run start.

## What changes

1. **Workflow model (DB-persisted).** A `Workflow` row = name + ordered list of **steps**
   (`agent`, `trigger: auto|manual`, `enabled`) + flags (`is_default`, `disabled`). Stored as JSON
   steps so reordering/insertion is cheap. Registered in the **admin portal**.

2. **Versioning / snapshot-on-execute.** Editing a workflow applies only to **new** runs. Each run
   **snapshots** the workflow definition it executed with (`RunRecord.workflow_snapshot` +
   `workflow_id`). The cockpit renders an executed run against its snapshot, so a run started as
   `A→B→D→C` keeps showing `A→B→D→C` even after the workflow is later edited to `A→B→C→D`.

3. **Soft-delete only.** Workflows are never hard-deleted — they're **disabled** (excluded from the
   run-page dropdown but still readable for historical runs).

4. **Default workflow.** Exactly one workflow may be `is_default`; it is **pre-selected** on the
   new-run page. If no workflow exists, the engine falls back to the **built-in default flow**
   (current hard-coded order, all-manual) so existing behavior is preserved.

5. **Builder UI (reorderable list).** A dedicated page (`/ui/workflows`) to **create / edit /
   clone / disable** workflows: a **drag-to-reorder** list of agent steps (Alpine + SortableJS,
   reusing the existing HTMX/Alpine/Tailwind stack — no node-canvas), each step with an auto/manual
   toggle and enable checkbox. (A full n8n-style node canvas is explicitly out of scope; the linear
   list matches the current linear pipeline.)

6. **Run-page selection.** A new optional **Workflow** dropdown on the new-run form, defaulting to
   the default workflow. The chosen workflow's order + per-agent triggers drive the run.

7. **Per-story execution control.** The workflow optionally specifies story execution: build a
   selected story only, or all stories in a chosen order, surfaced as per-story controls in the
   cockpit driven by the workflow definition.

8. **Precedence rule.** Per-agent configuration on the **Agents page** (DB `AgentPolicyRecord`)
   **overrides** the workflow's per-agent trigger — the Agents page is the global override; the
   workflow is the per-run default. Documented and enforced in policy resolution.

## Impact

- **Affected specs:** `workflows` (new), `hitl` (trigger source = workflow step), `api`
  (run-start accepts `workflow_id`), `story-fanout` (per-story order from workflow).
- **Affected code:** `src/ash/db/models.py` (+`Workflow`, `RunRecord.workflow_id`/
  `workflow_snapshot`), `src/ash/db/workflows.py` (new CRUD), `src/ash/admin/` (register
  `WorkflowAdmin`), `src/ash/config/settings.py` (policy resolution precedence + built-in default
  flow), `src/ash/graph/` (route/trigger driven by the run's snapshot, not the static order),
  `src/ash/web/routes.py` + templates (`workflows.html`, builder partials, run-form dropdown).
- **DB migration:** new `workflows` table + 2 `run_records` columns (stopgap `ADD COLUMN IF NOT
  EXISTS` backfill consistent with current practice in `db/base.py`).

## Out of scope

- Full node/graph canvas with branching/parallel edges (linear ordered steps only for v1).
- Conditional steps (run X only if Y), loops beyond the existing per-story loop.
- Cross-project shared workflow library / import-export.
