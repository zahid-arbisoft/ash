# Story Fan-out Specification

## Purpose
The story is the unit of execution inside one run. Stories execute sequentially in
dependency order via a `story_router` → `story_build` subgraph loop, each producing
one PR.

## Requirements

### Requirement: One PR Per Story
The system SHALL create exactly one PR per story, never duplicate PRs.

#### Scenario: First execution of a story
- GIVEN a story with no prior `branch` or `pr_url`
- WHEN the Coding agent runs
- THEN a new branch is created with a deterministic name `ash/<ticket_id>`
- AND a PR is opened and its URL stored in `StoryRecord.pr_url`

#### Scenario: Story re-run (regenerate)
- GIVEN a story with an existing `pr_url`
- WHEN the story is retried via `/ui/runs/{id}/stories/{ticket}/rerun`
- THEN the Coding agent pushes to the SAME branch
- AND updates the existing PR (no new PR created)

### Requirement: Dependency Ordering
The system SHALL execute stories in topological dependency order.

#### Scenario: Story with prerequisite
- GIVEN story B depends on story A
- WHEN the story router processes them
- THEN story A executes to completion before story B begins

### Requirement: Per-Story Retry
The system SHALL support retrying a single story from any step without re-running the whole run.

#### Scenario: Story retry from failed step
- GIVEN a story that failed at the `coding` step
- WHEN the client triggers a retry at `coding`
- THEN only the coding/reviewer/fixer steps re-run for that story
- AND the PM and research steps are not repeated

### Requirement: Story State Isolation
Each story's execution state SHALL be isolated in `WorkflowState.stories[ticket_id]`.

#### Scenario: Parallel story data
- GIVEN two stories running in sequence
- WHEN story B starts
- THEN story B's sub-states (research, coding, reviewer, fixer) are fresh/empty
- AND story A's sub-states are preserved independently

### Requirement: Analytics Tracking
The system SHALL record per-agent-per-story metrics (tokens in/out, latency, model).

#### Scenario: Metric persistence
- GIVEN an agent completing work for a story
- WHEN the agent node finishes
- THEN an `AgentRunMetric` row is written with tokens_in, tokens_out, duration_ms, model
- AND the run detail UI shows per-story chips with these totals
