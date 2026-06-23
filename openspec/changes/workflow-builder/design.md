# Design: Workflow Builder

## Context

The graph topology is currently static (`graph/builder.py`: `intake → pm → pm_publish → rfc →
plan_stories → story loop → merge`). Each agent self-gates via `BaseAgent._trigger_gate()`, which
resolves a policy (DB `AgentPolicyRecord` > `projects/<name>.yaml` > default manual). A Workflow
must change (a) which agents run and in what order and (b) each agent's trigger — **without**
rebuilding the compiled graph per run (the checkpointer is keyed per run and the graph is compiled
once at startup).

Key constraint: we keep ONE compiled graph. A workflow therefore drives behavior through **routing
+ per-agent skip**, not by recompiling edges:

- **Skip / reorder within the linear pipeline** is expressible because every agent already
  self-skips when its policy says so. A workflow that omits an agent ⇒ that agent's effective
  policy for the run is `enabled=false` (it self-skips with a note). A workflow's per-agent
  `trigger` ⇒ that agent's effective trigger for the run.
- **True reordering** (e.g. Dev before Research) is deferred: v1 supports **subset + per-agent
  trigger + skip** over the existing order, which covers the stated needs ("skip Research", "jump
  to Dev", "trigger RFC then stop"). Arbitrary permutation needs graph restructuring (decision to
  revisit; noted under Risks).

## Decisions

### D1 — Workflow as data, resolved into per-run effective policy
`Workflow.steps = [{agent, trigger, enabled}, ...]`. At run start the chosen workflow is **snapshot**
onto the run. `BaseAgent._resolve_policy()` gains a workflow layer so the effective policy is:

```
AgentPolicyRecord (Agents page, global override)   ← highest precedence
  > run's workflow snapshot step (per-run default)
    > projects/<name>.yaml
      > built-in default (manual, enabled)
```

This is the **precedence rule** the client asked for: Agents-page config wins over the workflow.
Resolution reads the run's `workflow_snapshot` (carried on `WorkflowState`), so it needs no graph
change — only a richer `_resolve_policy`.

### D2 — Snapshot-on-execute (versioning)
`RunRecord` gains `workflow_id` (FK, nullable) and `workflow_snapshot` (JSON copy of `steps` +
name + version at start). `WorkflowState` carries `workflow_snapshot` so agents resolve against the
frozen definition. Editing a `Workflow` bumps its `version` and rewrites `steps`; **existing runs
are untouched** (they read their snapshot). The cockpit rail orders/labels stages from the run's
snapshot when present, else the built-in order.

### D3 — Soft-delete + default
`Workflow.disabled: bool` (excluded from the run dropdown; still readable). `Workflow.is_default:
bool` with a single-default invariant enforced in the CRUD layer (setting one default clears the
others). New-run form pre-selects the default; "(built-in default flow)" is always offered as a
sentinel option when no workflow is chosen.

### D4 — Builder UI (reorderable list, not a canvas)
`/ui/workflows` lists workflows (name, step summary, default badge, enabled toggle). The editor is a
**SortableJS** drag-to-reorder list of agent chips, each with an auto/manual segmented toggle and an
enable checkbox; "Add step" offers agents not yet in the list. Save posts the ordered JSON to
`POST /ui/workflows` / `PUT /ui/workflows/{id}`. SortableJS is added to the existing vendored asset
set (or via CDN consistent with current Alpine/HTMX usage). Clone = duplicate then edit.

### D5 — Per-story execution
v1: the workflow may carry `story_execution: all | selected | one-by-one` advisory metadata that the
cockpit uses to default the per-story controls (which already exist). The per-story *order* reuses
the existing dependency topo-sort; manual one-by-one is already supported by the per-story trigger
gates. (No new graph machinery — this is a UI default + persisted preference.)

### D6 — API
`POST /runs` and `POST /ui/runs` accept an optional `workflow_id`. When present, the run snapshots
that workflow. Absent ⇒ default workflow if one exists, else built-in flow. No breaking change to
existing callers (field is optional).

## Risks / tradeoffs

- **Arbitrary reordering not supported in v1 (OD1 resolved: subset + trigger only).** A workflow
  controls, per agent, `enabled` (skip when off) and `trigger` (auto/manual); **execution always
  follows the canonical pipeline order** (intake→pm→rfc→research→dev→reviewer→fixer). The builder
  presents agents as a draggable list (honouring the requested DnD feel) and stores the authored
  order for forward-compat, but the engine ignores order in v1 and a clear inline note says so.
  True permutation (Dev-before-Research) needs graph restructuring and is deferred to a later
  change.
- **Single compiled graph.** Keeps resume/checkpoint semantics intact; the cost is the routing
  limitation above.
- **Snapshot drift.** Snapshots are immutable per run; a migration that changes the step schema must
  tolerate old snapshots (version field guards this).
- **Precedence confusion.** Two override layers (Agents page vs workflow) — must be clearly labelled
  in the UI ("Agents-page settings override the workflow").

## Migration / rollout

1. Add `workflows` table + `run_records.workflow_id`/`workflow_snapshot` (IF NOT EXISTS backfill).
2. Ship CRUD + admin registration + built-in-default fallback (no UI yet) — behavior identical to
   today when no workflow is selected.
3. Ship `_resolve_policy` workflow layer + snapshot-on-execute.
4. Ship the builder UI + run-form dropdown.
5. Per-story execution defaults.
