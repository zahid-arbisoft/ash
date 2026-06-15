# Best practices — high-quality spec generation

This project follows the engineering standards published in
[**arbisoft/ai-skillforge**](https://github.com/arbisoft/ai-skillforge/tree/main/Claude). Those
standards apply to ASH in two distinct ways, because ASH is itself a tool that *generates* specs:

1. **As a product requirement** — the PM agent must produce specs that meet the standard. This is
   enforced in code (see "How ASH enforces it" below), not just hoped for.
2. **As a developer convention** — engineers working *on* ASH use the same vendored Claude Code
   skills the rest of the org uses.

---

## Vendored skills (`.claude/skills/`)

Three skills from `ai-skillforge` are vendored into this repo so Claude Code picks them up when you
work here. They are kept **verbatim** so they track the upstream standard; provenance notes are
appended to each file.

| Skill | What it gives you |
|-------|-------------------|
| [`ai-first-engineering`](../.claude/skills/ai-first-engineering/SKILL.md) | The operating model: planning quality over typing speed, eval coverage over anecdote, review for system behavior. The principles the PM agent embodies. |
| [`blueprint`](../.claude/skills/blueprint/SKILL.md) | Turning an objective into cold-start-executable steps with an **acyclic dependency graph**, self-contained context briefs, and an adversarial review gate. |
| [`prompt-optimizer`](../.claude/skills/prompt-optimizer/SKILL.md) | A checklist for shaping a high-signal prompt: detect intent/scope, surface missing context, define acceptance criteria and scope boundaries. |

To install the broader org toolchain (more skills, language rules, reviewer agents) globally for
all your projects, follow the install guide in the
[ai-skillforge repo](https://github.com/arbisoft/ai-skillforge/tree/main/Claude).

---

## The spec-quality standard

A spec is only useful if a developer can pick up any ticket **cold** and execute it without asking
follow-up questions. Distilled from `ai-first-engineering` + `blueprint`, a good ASH spec:

1. **Invents no context.** It never assumes an existing app, framework, library, or codebase that
   the requirements didn't state. Unstated technology choices become a SPIKE or an open question.
2. **Honors every explicit signal.** A named reference product, UX tone, design principle,
   constraint, or guardrail in the requirements appears in at least one acceptance criterion or
   ticket. Named restrictions ("no screen recording", "no external calls") appear as explicit
   *negative* conditions in the epic acceptance criteria — not merely be absent from the spec.
3. **Is scoped to the ask.** "Prototype" / "MVP" / "proof of concept" gets a minimal slice on ONE
   platform — not a production system designed end-to-end or cross-platform adapters for all OSes
   unless multi-platform coverage is explicitly required.
4. **Flags unknowns instead of guessing.** Before finishing, the PM audits every item: Is any
   external format, API, integration, UI framework, or target platform referenced but not defined?
   All of these go into `open_questions`. On a greenfield project with external integrations or
   undecided technology choices, an empty `open_questions` is almost always a sign of
   under-auditing.
5. **Has an acyclic dependency graph.** Tickets depend only on real, earlier tickets. Foundational
   work (shared infra, data layer, encryption) has no dependencies. No cycles, no self-references.
6. **Assesses risk completely.** Any flow that sends data to an external system is evaluated for
   privacy, compliance, legal, and data-residency risk — not only functional risk. Any tool that
   monitors or collects user activity (window titles, keystrokes, location, browsing history)
   requires user consent and disclosure under privacy law (GDPR Art. 13, CCPA, local labor law)
   even for internal tools.

---

## How ASH enforces it

The standard is enforced in two layers, because prompts alone cannot *guarantee* structural
correctness.

### Layer 1 — prompt rules (the model's instructions)

The six rules above are baked into the PM agent's system prompt for both `raw_to_spec` and
`spec_ready` modes as `_QUALITY_RULES`, with a self-check step before the model finishes. See
[`src/ash/agents/pm.py`](../src/ash/agents/pm.py).

The `Spec` schema reinforces this: `Ticket.description` requires a cold-start-executable
description (motivation, files/modules touched, approach, out-of-scope, gotchas), `Ticket.dependencies`
documents the acyclic constraint, and `Spec.open_questions` gives the model a place to record
unknowns instead of inventing them. See [`src/ash/schemas.py`](../src/ash/schemas.py).

### Layer 2 — deterministic validation (the code's guarantee)

[`src/ash/agents/spec_validator.py`](../src/ash/agents/spec_validator.py) proves the parts that are
decidable in code, after generation:

- every `dependencies` entry references a real ticket id (no dangling refs),
- no ticket depends on itself,
- the dependency graph is **acyclic** (DFS cycle detection),
- ticket ids are unique,
- `type: spike` tickets have `needs_research: true` (the Research agent keys off both).

If validation fails, the PM agent runs **one self-correction round** — it feeds the exact errors
back to the model and regenerates. If problems still remain, they are appended to
`spec.open_questions` so a human sees them at the review gate. A structurally broken spec is never
shipped silently.

```
generate spec ──► validate ──► clean? ──► board + human review gate
                     │  fail
                     ▼
              feed errors back, regenerate once ──► re-validate
                                                       │ still failing
                                                       ▼
                                          surface in open_questions for human
```

This is the same shape as `blueprint`'s adversarial review gate, reduced to the checks that can be
made deterministic — which is exactly where automated enforcement beats a second LLM pass.

---

## Worked example — the issues this prevents

Two successive `raw_to_spec` runs on the same "local time-log assistant" requirement. The first
run (pre-hardening) scored 66/100; the second (post-hardening) scored 79/100. The remaining gaps
drove the rule improvements in this version.

**Run 1 → Run 2 (first hardening pass)**

| Defect in the generated spec | Rule violated | Now caught by |
|------------------------------|---------------|---------------|
| `T2` stored data "via EncryptionService" but `T3` (EncryptionService) depended on `T2` — a cycle | 5 | **Layer 2** (cycle detection → self-correction) |
| Tickets referenced an "existing Electron/React app" that the requirement never mentioned | 1 | Layer 1 (no-invented-context rule) |
| The requirement's named UX reference (Granola: "lightweight, ambient") was dropped entirely | 2 | Layer 1 (honor-every-signal rule) |
| A "working prototype" was scoped as a full production system | 3 | Layer 1 (scope-calibration rule) |
| "Compatible with Workstream's import format" — but that format was never defined | 4 | Layer 1 (flag-unknowns → `open_questions`) |
| Hybrid cloud-LLM mode raised no compliance/data-residency risk | 6 | Layer 1 (complete-risk-assessment rule) |

**Run 2 → Run 3 (second hardening pass — this version)**

| Defect in the second run | Rule violated | Now caught by |
|--------------------------|---------------|---------------|
| "No screen recording / no keystroke logging" guardrail missing from epic AC | 2 | Layer 1 (negative-AC rule — *new*) |
| "Working prototype" still got 3-OS signal adapters (Windows + macOS + Linux in T4) | 3 | Layer 1 (single-platform-prototype rule — *new*) |
| Workstream export format noted inline as "to be clarified" but not in `open_questions`; UI framework and target OS left unresolved | 4 | Layer 1 (audit-mandate rule — *new*); `open_questions` schema reinforced |
| Hybrid cloud-LLM path raised no user-consent or employment-monitoring legal risk | 6 | Layer 1 (activity-monitoring consent rule — *new*) |
