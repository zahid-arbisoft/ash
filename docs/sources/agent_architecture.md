# Multi-Agent SDLC System (LangGraph)

## Overview
This system automates software delivery using a multi-agent pipeline:
PM Agent → Ticket Breakdown → Dev Agent → Reviewer → Fixer Loop

---

## 1. PM Agent (Spec Builder)

### Responsibilities
- Requirement analysis
- Business goals
- Acceptance criteria
- Edge cases
- Technical design
- Risk analysis
- Ticket breakdown

### Output Schema
```json
{
  "epic": {},
  "technical_spec": {},
  "tickets": [],
  "risk_assessment": []
}
```

---

## 2. Dev Agent

### Trigger
`ticket.status == READY`

### Responsibilities
- Codebase exploration (ripgrep, tree-sitter, git history)
- Implementation planning
- Code changes (branch + commits)
- Testing (pytest, ruff, mypy)

### Output
```json
{
  "branch": "",
  "pr": {},
  "status": "open"
}
```

---

## 3. Reviewer Agent

### Trigger
PR opened

### Checks
- Code quality (SOLID, duplication, complexity)
- Security (auth, injection, secrets)
- Test coverage
- Spec compliance

### Output
```json
{
  "status": "approved | changes_requested",
  "issues": []
}
```

---

## 4. Fixer Agent

### Trigger
Review comments

### Responsibilities
- Read review feedback
- Apply minimal patches
- Re-run tests
- Push updates

---

## Workflow (LangGraph)

```
PM Agent
   ↓
Ticket Queue
   ↓
Dev Agent
   ↓
PR Created
   ↓
Reviewer
   ↓
Fixer (if needed)
   ↓
Reviewer → Merge
```

---

## Tech Stack

### Core
- Python 3.12
- LangGraph
- LangChain
- LLMs (GPT-5 / Claude Opus)

### Storage
- PostgreSQL (system of record)
- pgvector (code memory)

### Queue
- Redis

### GitHub Integration
- GitHub API
- GitPython

### Code Intelligence
- ripgrep
- tree-sitter
- AST analysis
- semantic search (optional)

### Observability
- LangSmith

---

## Data Model (State)

```python
class WorkflowState:
    requirement: str
    technical_spec: dict
    tickets: list
    current_ticket: dict
    pr_url: str
    review_comments: list
```

---

## MVP Strategy

### Phase 1
- PM Agent
- Dev Agent
- Human review

### Phase 2
- Add Reviewer Agent

### Phase 3
- Add Fixer Loop

---

## Key Insight

The PM Agent quality determines the success of the entire system.
Weak specs = broken automation downstream.
