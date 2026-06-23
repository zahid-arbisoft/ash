# API Specification

## Purpose
FastAPI REST + Jinja2 UI surfaces run management, connector configuration,
story oversight, and approvals to human clients.

## Requirements

### Requirement: Run Lifecycle Endpoints
The system SHALL expose REST endpoints to start, inspect, resume, and stop runs.

#### Scenario: Start a run
- GIVEN valid run parameters (project, intake_mode, issue_id)
- WHEN `POST /runs` is called
- THEN a run is created with a unique UUID
- AND the graph starts as a background task
- AND the run ID is returned immediately (202 response)

#### Scenario: Read run status
- GIVEN an existing run ID
- WHEN `GET /runs/{id}` is called
- THEN the current graph state and agent outputs are returned
- AND the response includes story-level status chips

#### Scenario: Resume a paused run
- GIVEN a run paused at a HITL gate
- WHEN `POST /runs/{id}/resume` is called with the resume payload
- THEN the graph resumes from the interrupt point

### Requirement: Story Rerun Endpoint
The system SHALL allow per-story regeneration without restarting the whole run.

#### Scenario: Rerun a story from a step
- GIVEN a story that failed or needs regeneration
- WHEN `POST /ui/runs/{id}/stories/{ticket}/rerun` is called with `from_step`
- THEN only that story's steps from `from_step` onward are re-executed

### Requirement: UI Run List Pagination
The UI run list SHALL be paginated.

#### Scenario: Large run history
- GIVEN more than one page of runs
- WHEN the client navigates to `/ui/runs`
- THEN runs are displayed in pages
- AND navigation controls allow moving between pages

### Requirement: SSE Run Detail
The run detail page SHALL stream live updates via Server-Sent Events.

#### Scenario: Live run monitoring
- GIVEN a run in progress
- WHEN the client opens the run detail page
- THEN agent output appears in real time via SSE
- AND the page does not require manual refresh
