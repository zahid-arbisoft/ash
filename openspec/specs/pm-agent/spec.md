# PM Agent Specification

## Purpose
The PM agent translates raw requirements or pre-written specs into structured tickets
(Epics + Stories) that are published to the configured Board sink for client oversight.

## Requirements

### Requirement: Two-Phase PM Execution
The PM node SHALL execute in two graph nodes: `pm` (generate/extract) and `pm_publish` (interrupt → review → push).

#### Scenario: Spec generation (raw_to_spec)
- GIVEN `intake_mode=raw_to_spec`
- WHEN the `pm` node runs
- THEN it generates a full Spec (title, summary, epics, stories, risks)
- AND writes the spec to the Board sink
- AND checkpoints before the review gate

#### Scenario: Ticket extraction (spec_ready)
- GIVEN `intake_mode=spec_ready` with a pre-written spec
- WHEN the `pm` node runs
- THEN it extracts tickets from the spec without re-writing it
- AND does NOT apply the raw spec generation prompt

### Requirement: PM Review Gate
The system SHALL pause at `pm_publish` for human review before pushing tickets to the connector.

#### Scenario: Human approves spec
- GIVEN the run is paused at the PM review gate
- WHEN the client approves the spec in the UI
- THEN tickets are pushed to the configured Board connector (Jira/Plane/file)
- AND the run continues to the story fan-out phase

#### Scenario: Human rejects spec
- GIVEN the run is paused at the PM review gate
- WHEN the client rejects the spec
- THEN the run is marked rejected and halts
- AND no tickets are pushed to the connector

### Requirement: Post-PM Story Selection
The system SHALL allow the client to select a subset of stories at the PM review gate.

#### Scenario: Partial story selection
- GIVEN a spec with 5 stories
- WHEN the client selects 3 stories for this run
- THEN only those 3 stories proceed to the story fan-out phase
- AND the remaining 2 stories are excluded from this run's execution

### Requirement: Story Mode
The PM agent MUST respect the `story_mode` config (`single` or `multiple`).

#### Scenario: Single story mode (default)
- GIVEN `story_mode=single`
- WHEN the PM generates a spec
- THEN it produces exactly one story per epic

#### Scenario: Multiple story mode
- GIVEN `story_mode=multiple`
- WHEN the PM generates a spec
- THEN it may produce multiple stories with dependency ordering
