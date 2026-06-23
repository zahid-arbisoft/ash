# Web UI Specification (delta)

## ADDED Requirements

### Requirement: Run Cockpit
The system SHALL provide a single run cockpit page per run that presents the whole agent
pipeline and lets the client drive it.

#### Scenario: Open a run
- GIVEN a run id
- WHEN the client opens `/ui/run/{run_id}`
- THEN a pipeline rail shows the ordered stages (intake, pm, rfc, research, dev, reviewer, fixer)
- AND each stage shows a status dot and a token chip
- AND the selected stage's workbench panel is shown below the rail

#### Scenario: Deep-link a stage
- GIVEN a run id and a stage name
- WHEN the client opens `/ui/run/{run_id}/{stage}`
- THEN the cockpit opens with that stage selected
- AND the page works without client-side JavaScript

#### Scenario: Live updates
- GIVEN a run with a running agent
- WHEN the client views the cockpit
- THEN the rail and active panel update over an SSE stream until the run reaches a terminal or awaiting state

#### Scenario: Per-story build stages
- GIVEN a run with more than one story
- WHEN the client views a build stage (research/dev/reviewer/fixer)
- THEN the rail shows a story selector
- AND the build stage status reflects the selected story

### Requirement: Uniform Per-Agent Controls
Every agent stage panel SHALL expose the same control surface: trigger, stop, restart,
re-trigger, and HITL feedback.

#### Scenario: Trigger a gated agent
- GIVEN an agent paused at a manual-trigger gate
- WHEN the client clicks Trigger on that stage
- THEN the run resumes and the agent runs

#### Scenario: Stop a running agent
- GIVEN an agent running in a stage
- WHEN the client clicks Stop
- THEN the in-flight task is cancelled and the checkpoint is preserved

#### Scenario: Restart a stopped run
- GIVEN a run that was stopped
- WHEN the client clicks Restart
- THEN the run resumes from its last checkpoint

#### Scenario: Re-trigger with custom prompt
- GIVEN an agent that has already produced output
- WHEN the client re-triggers it with an optional custom prompt
- THEN the agent runs again for that stage (or story) with the custom prompt folded into its instructions
- AND any existing branch/PR for the story is preserved (no duplicate PR)

### Requirement: Custom Prompt At Run Start
The new-run form SHALL accept an optional free-text custom prompt that is threaded into the
first agent's instructions.

#### Scenario: Start a run with a custom prompt
- GIVEN the new-run form
- WHEN the client enters a custom prompt and starts the run
- THEN the prompt is stored on the run state as `run_prompt`
- AND it is folded into the PM agent's instructions (or the first build agent for raw_to_dev)

### Requirement: Source/Destination New-Run Form
The new-run form SHALL be organized into a Source section and a Destination & options
section, with advanced options collapsed by default.

#### Scenario: Minimal run
- GIVEN the new-run form
- WHEN the client selects a project and provides an item id or attachment
- THEN the run can be started without expanding advanced options
- AND destination/story-mode/intake-mode use sensible defaults

### Requirement: Dedicated I/O Log Page
The system SHALL provide a global I/O log page and a per-run I/O view that surface every
LLM exchange with its sent context, sent code, received output, and token sizes.

#### Scenario: Filter the global I/O log
- GIVEN exchanges recorded across runs
- WHEN the client opens `/ui/io` and filters by run, agent, story, or phase
- THEN only matching exchanges are shown
- AND a token-analytics header summarizes totals for the filtered set

#### Scenario: Inspect a Dev exchange
- GIVEN a Dev agent exchange that sent code to the model
- WHEN the client expands the exchange
- THEN the sent code is shown as a labeled block distinct from other context
- AND the exchange shows prompt and completion token counts and the code block's size

### Requirement: Redesigned Run Index
The system SHALL provide a run hub list as the canonical index of runs, replacing the old
runs timeline page and runs table.

#### Scenario: Find a run
- GIVEN existing runs
- WHEN the client opens `/ui/runs`
- THEN a list shows each run with status, current stage, project, tokens, and age
- AND each row links to that run's cockpit

#### Scenario: Old run paths redirect
- GIVEN a bookmarked old path `/ui/runs/{id}` or `/ui/pm/{id}` or `/ui/dev/{id}`
- WHEN the client opens it
- THEN the client is redirected to the corresponding cockpit view
