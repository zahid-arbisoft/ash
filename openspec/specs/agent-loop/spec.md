# Agent Loop Specification

## Purpose
The core LangGraph-based orchestration loop that sequences agents (PM → RFC → Research →
Coding → Reviewer → Fixer) for a single run, with configurable intake routing and
human-in-the-loop gates.

## Requirements

### Requirement: Intake Routing
The system SHALL route each run to the correct subgraph based on `intake_mode`.

#### Scenario: raw_to_spec mode
- GIVEN a run with `intake_mode=raw_to_spec`
- WHEN the graph starts
- THEN the PM agent runs first to generate a spec and extract tickets
- AND the run pauses at the PM review gate before continuing

#### Scenario: spec_ready mode
- GIVEN a run with `intake_mode=spec_ready`
- WHEN the graph starts
- THEN the PM agent runs in extract-tickets mode (no spec generation)
- AND tickets are extracted from the pre-written spec

#### Scenario: raw_to_dev mode
- GIVEN a run with `intake_mode=raw_to_dev`
- WHEN the graph starts
- THEN the PM agent is skipped entirely
- AND the graph proceeds directly to the story fan-out phase

### Requirement: Generic Engine
The system SHALL contain no hardcoded project names or repository references in `src/`.

#### Scenario: Project configuration isolation
- GIVEN two projects with different repositories and connectors
- WHEN both are running simultaneously
- THEN each run uses only its own `projects/<name>.yaml` config
- AND no state leaks between runs

### Requirement: LangGraph-only Control Flow
The system SHALL model all orchestration as LangGraph graph nodes, edges, and subgraphs.

#### Scenario: Conditional routing
- GIVEN a conditional branch in the workflow (e.g. skip RFC agent)
- WHEN the condition is evaluated
- THEN the routing MUST be implemented as a LangGraph conditional edge
- AND NOT as a Python if/else outside the graph

### Requirement: Run Persistence
The system SHALL persist run state to Postgres via the LangGraph AsyncPostgresSaver checkpointer.

#### Scenario: Resume after crash
- GIVEN a run that was interrupted mid-execution
- WHEN the system restarts
- THEN the run can be resumed from the last checkpoint
- AND no story work is duplicated

### Requirement: Concurrent Run Safety
The system SHALL support multiple simultaneous runs without connection contention.

#### Scenario: Parallel runs
- GIVEN two runs executing simultaneously
- WHEN both access the Postgres checkpointer
- THEN each uses a separate connection from the pool
- AND neither receives "another command is already in progress" errors
