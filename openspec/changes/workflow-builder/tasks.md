# Tasks: Workflow Builder

Phased so the app stays runnable after each phase. Behavior is identical to today until a workflow
is created and selected.

## Phase A — Data + resolution (no UI)  ✅ DONE
- [x] A1. `Workflow` model in `db/models.py` (`name`, `description`, `steps` JSON, `story_execution`,
  `is_default`, `disabled`, `version`, timestamps) + `WorkflowAdmin` registered.
- [x] A2. `RunRecord.workflow_id` + `workflow_snapshot` (JSON) + PG backfills.
- [x] A3. `db/workflows.py` CRUD (list/get/create/update-bump-version/set_default single-invariant/
  disable/enable/clone/default_workflow) + `normalize_steps`/`snapshot_for`/`step_policy`. Tests.
- [x] A4. Built-in default flow (`default_steps` — every agent enabled+manual) when no workflow.
- [x] A5. `WorkflowState.workflow_snapshot`; `start_run(workflow_snapshot=…)`.
- [x] A6. `BaseAgent._resolve_policy` precedence DB > workflow > YAML > default. Tests per layer.

## Phase B — Run-start integration  ✅ DONE
- [x] B1. `POST /ui/runs` accepts `workflow_id`; snapshots the chosen (or default) workflow onto the
  run + persists `workflow_id`/`workflow_snapshot` on RunRecord. (External `POST /runs` unchanged;
  add later if needed.)
- [x] B2. New-run form Workflow dropdown (default pre-selected; built-in sentinel).
- [x] B3. Cockpit reflects the snapshot: workflow-disabled agents render `skipped` in the rail
  (`_wf_disabled`), workflow name + story_execution shown in the header. Tests.

## Phase C — Builder UI  ✅ DONE
- [x] C1. `/ui/workflows` list (name, step summary, default/disabled badges, version, story_execution).
- [x] C2. Editor (create + inline edit): per-agent enable + auto/manual (authoritative, no-JS-safe)
  + SortableJS drag as progressive enhancement (sets `order`, forward-compat); "runs in pipeline
  order" note (OD1).
- [x] C3. `POST /ui/workflows` (+ `/{id}/update|default|disable|enable|clone`).
- [x] C4. Sidebar + mobile nav "Workflows" entry.

## Phase D — Per-story execution + docs  ✅ DONE
- [x] D1. `story_execution` (all|selected|one_by_one) stored on the snapshot + surfaced in the
  cockpit header. v1 advisory: `one_by_one` aligns with the existing manual build-agent gates (each
  story already waits for a manual trigger); deeper auto-sequencing deferred.
- [x] D2. Plan §7 Locked Decision #34 + §11 Changelog; `CLAUDE.md` status; specs in this change.
- [x] D3. `openspec validate workflow-builder --strict` valid; full pytest green.

## Open decisions
- [x] OD1 — RESOLVED (2026-06-24, user): **subset + per-agent trigger only**. A workflow controls
  per-agent enable + auto/manual; execution stays in canonical pipeline order. Builder shows a
  draggable list (stores authored order for forward-compat) with an inline "runs in pipeline order"
  note. True permutation deferred.
