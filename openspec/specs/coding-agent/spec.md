# Coding Agent Specification

## Purpose
The Coding agent implements a story's technical spec into code changes, runs tests,
and opens (or updates) a PR using a bounded create_agent tool loop.

## Requirements

### Requirement: Bounded Tool Loop
The Coding agent SHALL run a bounded `create_agent` tool loop with a configurable max iterations.

#### Scenario: Test-fix loop
- GIVEN a story implementation that fails tests on the first attempt
- WHEN the Coding agent detects test failure
- THEN it retries up to `MAX_CODE_ITERATIONS=3` times
- AND halts with a failure status if tests still fail after max iterations

### Requirement: Deterministic Branch Naming
The Coding agent SHALL use a deterministic branch name per story.

#### Scenario: Branch naming
- GIVEN a story with `ticket_id=PROJ-42`
- WHEN the Coding agent creates a branch
- THEN the branch is named `ash/PROJ-42`
- AND if the branch already exists, it is reused (not duplicated)

### Requirement: Context-Minimal Code Reading
The Coding agent SHALL read only line-ranged chunks of files, not whole files.

#### Scenario: Reading a relevant function
- GIVEN a file with 500 lines where only lines 120-180 are relevant
- WHEN the Coding agent reads that function
- THEN `read_file(path, start=120, end=180)` is called
- AND the full 500-line file is NOT sent to the LLM

### Requirement: Dev Toolkit
The Coding agent SHALL have access to a standard DevToolkit.

#### Scenario: Available tools
- GIVEN a Coding agent running a story
- WHEN it needs to explore the codebase
- THEN it has access to: `read_file`, `list_files`, `search_code`, `run_command`
- AND these tools are the only file/shell access the agent has

### Requirement: PR Template Respect
The Coding agent SHOULD detect and use the repo's PR template if present.

#### Scenario: PR template exists
- GIVEN a repo with `.github/pull_request_template.md`
- WHEN the Coding agent opens a PR
- THEN the PR body is populated using the detected template structure
