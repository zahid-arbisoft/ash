# Connectors Specification

## Purpose
Pluggable connectors decouple the engine from specific issue trackers, boards, and
tool servers. A single `Connector` row can act as source, sink, or both.

## Requirements

### Requirement: Unified Connector Model
The system SHALL use a single `Connector` database model for all integration types.

#### Scenario: Connector as both source and sink
- GIVEN a Jira connector with `is_source=true` and `is_sink=true`
- WHEN a run is configured to use it
- THEN issues are read from Jira (source) AND tickets are pushed to Jira (sink)
- AND only one `Connector` row exists for this integration

### Requirement: Pluggable by Config
The system SHALL select connectors via run/project config — not code changes.

#### Scenario: Switching board sink
- GIVEN a project previously using file-based board output
- WHEN the project config is updated to reference a Plane connector
- THEN all new runs for that project push tickets to Plane
- AND no source code changes are required

### Requirement: MCP Tool Loading
The system SHALL load tools from HTTP MCP connectors via `langchain-mcp-adapters`.

#### Scenario: MCP HTTP connector
- GIVEN a `Connector` with `transport=http` pointing to an MCP server URL
- WHEN an agent needs tools from this connector
- THEN `mcp_tools_for(connector_id)` loads and returns the tool list
- AND the agent can invoke those tools in its create_agent loop

### Requirement: Connector Health Check
The system SHALL expose a health check endpoint for each connector.

#### Scenario: Healthy connector
- GIVEN a configured connector
- WHEN `GET /ui/connectors/{id}/health` is called
- THEN a success indicator is returned if the connector is reachable

#### Scenario: Unreachable connector
- GIVEN a connector whose remote is down
- WHEN health check is called
- THEN a failure status is returned without crashing the app

### Requirement: Per-Kind Config Schemas
The system SHALL validate connector configuration using discriminated per-kind schemas.

#### Scenario: Jira connector validation
- GIVEN a connector form submission with `kind=jira`
- WHEN the config is validated
- THEN it MUST include `base_url`, `project_key`, and `api_token`
- AND fields specific to GitHub connectors are NOT required
