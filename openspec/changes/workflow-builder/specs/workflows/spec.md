# Workflows Specification (delta)

## ADDED Requirements

### Requirement: Workflow Definition
The system SHALL persist named **workflows**, each an ordered list of agent steps where every step
declares an agent, a trigger mode (`auto` or `manual`), and an enabled flag.

#### Scenario: Create a workflow
- GIVEN a client on the workflow builder
- WHEN they save a workflow with an ordered set of agent steps and per-step trigger/enabled
- THEN a `Workflow` row is persisted with those steps and `version = 1`
- AND it appears in the run-page workflow dropdown

### Requirement: Default Workflow
The system SHALL allow at most one workflow to be marked default, and SHALL pre-select it on the
new-run page. When no workflow exists, the system SHALL fall back to the built-in default flow
(current agent order, all agents manual).

#### Scenario: Single default invariant
- GIVEN workflow A is the default
- WHEN the client marks workflow B as default
- THEN B becomes default AND A is no longer default

#### Scenario: Pre-selection on run page
- GIVEN a default workflow exists
- WHEN the client opens the new-run form
- THEN that workflow is pre-selected in the workflow dropdown

#### Scenario: No workflow defined
- GIVEN no workflow exists
- WHEN a run starts
- THEN the run executes the built-in default flow

### Requirement: Soft Delete
Workflows SHALL NOT be hard-deleted. A workflow MAY be disabled, which excludes it from the run-page
dropdown while keeping it readable for historical runs.

#### Scenario: Disable excludes from dropdown
- GIVEN an enabled workflow used by past runs
- WHEN the client disables it
- THEN it no longer appears in the new-run dropdown
- AND runs that executed it still render against their snapshot

### Requirement: Snapshot On Execute (Versioning)
A run SHALL snapshot the workflow definition it executes with. Editing a workflow SHALL apply only
to new runs; existing runs SHALL continue to render and resolve policy against their snapshot.

#### Scenario: Edit does not affect past runs
- GIVEN run R executed workflow W as `A→B→D→C`
- WHEN W is later edited to `A→B→C→D`
- THEN R still shows `A→B→D→C`
- AND a new run started with W shows `A→B→C→D`

### Requirement: Run-Page Workflow Selection
The new-run form SHALL offer an optional workflow dropdown (defaulting to the default workflow), and
the selected workflow SHALL drive the run's agent order and per-agent trigger modes.

#### Scenario: Select a non-default workflow
- GIVEN multiple enabled workflows
- WHEN the client selects one other than the default and starts a run
- THEN the run snapshots and executes that workflow

### Requirement: Configuration Precedence
Per-agent configuration set on the Agents page (DB policy override) SHALL take precedence over the
trigger/enabled values defined in a run's workflow.

#### Scenario: Agents-page override wins
- GIVEN a workflow step sets agent `research` to `trigger=auto`
- AND the Agents page sets `research` to `trigger=manual`
- WHEN a run using that workflow reaches research
- THEN research gates manually (the Agents-page value wins)

### Requirement: Per-Story Execution Control
A workflow's story-execution preference (all stories, a selected story, or one-by-one) SHALL set the
default per-story controls in the cockpit, and per-story manual triggering SHALL remain available
regardless of the preference.

#### Scenario: One-by-one default
- GIVEN a workflow with `story_execution = one-by-one`
- WHEN a multi-story run reaches the build stages
- THEN each story waits for a manual trigger by default
