# Human-in-the-Loop (HITL) Specification

## Purpose
Human oversight gates pause the graph at configurable checkpoints, route decisions
back to the client via the UI, and resume execution on explicit approval.

## Requirements

### Requirement: Single-Flag Toggle
Human approval gates SHALL be controlled by a single `ApprovalGate` flag per agent/node.

#### Scenario: Gate enabled
- GIVEN an agent with `trigger=manual`
- WHEN the graph reaches that agent node
- THEN `interrupt()` is called and the run pauses
- AND the UI shows an "Approve" action for the client

#### Scenario: Gate disabled (autonomous)
- GIVEN an agent with `trigger=auto` (default)
- WHEN the graph reaches that agent node
- THEN the agent runs without pausing
- AND no human action is required

### Requirement: Resume via UI
The system SHALL resume a paused run when the client approves via the UI.

#### Scenario: Client approves
- GIVEN a run paused at a HITL gate
- WHEN the client clicks Approve at `/ui/runs/{id}/trigger`
- THEN `POST /ui/runs/{id}/trigger` sends `Command(resume="run")`
- AND the graph continues from the interrupted node

### Requirement: Merge Approval Gate
The system SHALL support a separate human gate for PR merge when configured.

#### Scenario: Merge requires human approval
- GIVEN `auto_merge_on_approve=True` and `require_human_for_merge=True`
- WHEN the Reviewer approves a PR
- THEN the run pauses before merging
- AND the client approves at `/ui/runs/{id}/approve`
- AND the merge proceeds only after approval

### Requirement: No Scattered Gate Checks
The system SHALL NOT scatter `if human:` / `if trigger==manual:` checks outside the `_trigger_gate()` method on `BaseAgent`.

#### Scenario: Adding a new agent gate
- GIVEN a new agent that needs a manual trigger option
- WHEN implementing the gate
- THEN the implementation MUST call `BaseAgent._trigger_gate()` — no ad-hoc interrupt calls elsewhere
