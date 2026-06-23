# Human-in-the-Loop (HITL) Specification (delta)

## MODIFIED Requirements

### Requirement: Single-Flag Toggle
Human approval gates SHALL be controlled by a single per-agent `trigger` policy, and the
default for every agent SHALL be `manual`.

#### Scenario: Gate enabled (default)
- GIVEN an agent with the default policy (`trigger=manual`)
- WHEN the graph reaches that agent node
- THEN `interrupt()` is called and the run pauses with `status=awaiting_trigger`
- AND the cockpit shows a Trigger action for that agent

#### Scenario: Gate disabled (opt-in autonomous)
- GIVEN an agent whose DB/YAML policy sets `trigger=auto`
- WHEN the graph reaches that agent node
- THEN the agent runs without pausing

#### Scenario: PM is manual by default
- GIVEN a freshly started run with no policy overrides
- WHEN intake completes
- THEN the run pauses at the PM stage awaiting trigger
- AND no spec is generated until the client triggers PM

#### Scenario: Intake is always automatic
- GIVEN a freshly started run
- WHEN the run begins
- THEN intake fetches the issue without a trigger gate

## ADDED Requirements

### Requirement: Per-Agent HITL Feedback
The system SHALL let the client supply free-text feedback to any agent (and to any
individual story for build agents) to drive a refined re-run.

#### Scenario: Refine a run-level agent
- GIVEN a completed PM or RFC stage
- WHEN the client submits feedback for that agent
- THEN the agent re-runs with the feedback folded into its instructions
- AND the feedback is consumed once (cleared after the run)

#### Scenario: Refine a per-story build agent
- GIVEN a built story (research/dev/reviewer/fixer output exists)
- WHEN the client submits feedback for that agent on that story
- THEN that story is reset from the chosen step onward (preserving branch/PR)
- AND the agent re-runs with the feedback
- AND downstream build stages for that story re-run on the new output
