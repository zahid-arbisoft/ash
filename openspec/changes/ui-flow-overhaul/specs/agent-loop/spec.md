# Agent Loop Specification (delta)

## ADDED Requirements

### Requirement: Custom Prompt Threading
Agents SHALL fold an optional per-agent custom prompt into their instructions for a single
pass, then clear it (consume-once).

#### Scenario: Custom prompt applied once
- GIVEN a run with `custom_prompts[agent]` set
- WHEN that agent runs
- THEN the custom prompt is appended to the agent's instructions for that pass
- AND the custom prompt is cleared from state after the agent returns

### Requirement: Code-to-LLM Capture
Build agents (Dev, Research, Fixer) SHALL record which code and context were sent to the
model in each exchange, alongside token counts.

#### Scenario: Dev records sent code
- GIVEN the Dev agent generating a change
- WHEN it calls the model with code grounding
- THEN the exchange stores the sent code in the `code` field
- AND the brief/spec in the `context` field
- AND the prompt and completion token counts

#### Scenario: I/O viewer surfaces code size
- GIVEN a recorded Dev exchange with a `code` field
- WHEN the client inspects it on the I/O page
- THEN the code block and its size are shown distinctly from other sent context

### Requirement: Dev No-Output Is A Step Failure
When the Dev agent produces no code edits (so no PR can be opened), it SHALL record an error with a
human-readable reason and the story SHALL be marked failed, and the downstream Reviewer and Fixer
SHALL self-skip rather than run on an empty change.

#### Scenario: Dev produces nothing
- GIVEN the Dev agent whose change set is empty
- WHEN it returns
- THEN its namespace carries an `error` explaining no PR was opened
- AND the story status is `failed`

#### Scenario: Reviewer/Fixer skip without a change
- GIVEN a story whose Dev change is empty or absent
- WHEN Reviewer or Fixer runs
- THEN it self-skips with a note instead of reviewing/fixing nothing

### Requirement: Combined Single PR Across Stories
A multi-story run SHALL support a `pr_strategy` of `single`, where all built stories share one
run-level branch and worktree and contribute commits to ONE combined PR, persisted on the run so the
choice survives restarts. The default `per_story` SHALL keep one PR per story.

#### Scenario: Stories stack into one PR
- GIVEN a multi-story run with `pr_strategy = single`
- WHEN the first story's Dev runs
- THEN it opens one PR and records the shared branch/worktree/PR url at run level
- AND each subsequent story reuses the shared branch and updates the same PR instead of opening a new one

#### Scenario: Strategy persists
- GIVEN a run started (or approved) with `pr_strategy = single`
- WHEN the server restarts and the run is reopened
- THEN the run still resolves to the combined-PR strategy

## MODIFIED Requirements

### Requirement: Dev Agent (renamed from Coding Agent)
The build/implementation agent SHALL be named "Dev" in all client-facing surfaces and as
its canonical agent name, while historical records under the prior name remain readable.

#### Scenario: Dev agent canonical name
- GIVEN the implementation agent
- WHEN it runs or is configured
- THEN its agent name is `dev`
- AND policy config under `agents.dev` applies

#### Scenario: Historical coding records still display
- GIVEN historical exchanges or tasks recorded with agent name `coding`
- WHEN they are listed in the UI
- THEN they are displayed under the "Dev" label
