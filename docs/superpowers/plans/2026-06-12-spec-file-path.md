# Spec File Path Implementation Plan

> **Status: IMPLEMENTED.** This plan reflects the final implemented state, not the original draft.
> Key divergences from the original draft are noted inline.

**Goal:** Allow an admin to upload a spec document (`.md`, `.txt`, `.pdf`, `.docx`) through `/admin/spec-upload`, trigger a standalone run (no issue source, no project required), and have the PM agent convert the document into a structured `Spec`. Tickets from the spec are optionally created in a selected integration.

**Architecture:** New `intake_mode = "spec_file"` routes through PM. Intake skips issue fetch. File stored at `runtime/uploads/<uuid>.<ext>` (absolute path). PM converts to Markdown via `utils/file_extract.to_markdown`, then LLM → `Spec`. If `integration_id` is set, PM creates each ticket via `provider.create_issue`. Upload form is a SQLAdmin `BaseView` (not a Starlette router).

**Tech Stack:** FastAPI, SQLAdmin BaseView, Starlette, LangGraph, LangChain structured output, SQLAlchemy async, Pydantic, pypdf, python-docx

---

## File Map

| File | Change |
|------|--------|
| `src/ash/graph/state.py` | Add `spec_file_path`, make `item_id`/`project` optional, add `"spec_file"` to `IntakeMode`; `PMState.ticket_refs` replaces `board_ref` |
| `src/ash/db/models.py` | `RunRecord.item_id` nullable, add `spec_file_path` column |
| `src/ash/api/schemas.py` | `RunRequest.item_id` optional, add `spec_file_path` |
| `src/ash/graph/runner.py` | Add `item_id: str \| None`, `spec_file_path: str \| None` params |
| `src/ash/agents/intake.py` | Skip issue fetch for `spec_file` mode |
| `src/ash/graph/builder.py` | Route `spec_file` → PM |
| `src/ash/agents/pm.py` | Handle `spec_file` mode: `to_markdown` → LLM → `Spec`; create tickets via `provider.create_issue` |
| `src/ash/integrations/base.py` | Add `create_issue(title, body) -> str` to `IssueProvider` protocol |
| `src/ash/integrations/github.py` | Implement `create_issue` |
| `src/ash/integrations/plane.py` | Implement `create_issue` |
| `src/ash/integrations/jira.py` | Implement `create_issue` |
| `src/ash/utils/file_extract.py` | **New** — `to_markdown(path)` for MD/TXT/PDF/DOCX |
| `src/ash/utils/__init__.py` | **New** — package init |
| `src/ash/admin/spec_upload.py` | **New** — `SpecUploadView(BaseView)` |
| `src/ash/admin/templates/spec_upload.html` | **New** — Jinja2 template (file input + integration dropdown) |
| `src/ash/admin/__init__.py` | `admin.add_base_view(SpecUploadView)`; set `admin.admin.state.outer_app = app` |
| `src/ash/api/app.py` | Wire `configure_logging` in lifespan |
| `src/ash/utils/logging.py` | **New** — `configure_logging(level)` |
| `pyproject.toml` | Add `pypdf>=4.0`, `python-docx>=1.1` |
| `tests/graph/test_state.py` | Tests for new fields |
| `tests/graph/test_runner.py` | Tests for optional `item_id`, `spec_file_path` |
| `tests/graph/test_intake_routing.py` | Test `spec_file` routes to PM |
| `tests/agents/test_intake_spec_file.py` | Intake skips fetch in `spec_file` mode |
| `tests/agents/test_pm.py` | PM reads file + converts; PM creates tickets via integration |
| `tests/admin/test_spec_upload.py` | Upload form GET/POST, file saved, RunRecord created |
| `tests/integrations/test_providers.py` | `create_issue` for GitHub, Plane, Jira |
| `tests/utils/test_file_extract.py` | **New** — `to_markdown` for all four formats |

---

## Key Implementation Notes

### SQLAdmin `BaseView` vs Starlette router

Original design used a custom Starlette route. Implemented as SQLAdmin `BaseView` so the upload appears in the admin sidebar alongside other admin views.

Inside a `BaseView`, `request.app` is SQLAdmin's inner Starlette app, **not** the outer FastAPI app. The runner is accessed via:

```python
outer_app = request.app.state.outer_app
runner: Runner = outer_app.state.runner
```

Set in `setup_admin`:
```python
admin.admin.state.outer_app = app
```

### No project required

Original design required a project selection and stored `spec_file_path` as a relative path under `runtime/<project>/specs/`. Final implementation requires no project — files are stored at an absolute path `runtime/uploads/<uuid>.<ext>` and `spec_file_path` on state is this absolute path.

### File extraction

All formats converted to Markdown before the LLM call via `utils/file_extract.to_markdown`. Blocking I/O runs in `asyncio.to_thread` to satisfy ASYNC240.

```python
content = await asyncio.to_thread(_read_spec_file, Path(state.spec_file_path))
```

### Ticket creation via integration

After generating the spec, if `integration_id` is set, PM calls `provider.create_issue(title, body)` for each ticket. `IssueProvider` protocol gained `create_issue`; all three providers implement it. Returns `ticket_refs: list[str]` in the PM state namespace.

### Local spec file write

PM always writes the spec to `RUNTIME_DIR/board/` as a local `.md`/`.json` record. This is a fixed side-effect, not configurable. No `board_ref` is surfaced in state — `PMState.ticket_refs` replaced `PMState.board_ref`.

### Redirect target

`request.url_for("admin:list", identity="run-record")` — SQLAdmin derives identity from the model class name (`RunRecord` → `run-record`), not the display `name = "Run"`.

---

## State shape after this feature

```python
# WorkflowState additions
project: str = ""              # optional for spec_file runs
item_id: str = ""              # optional for spec_file runs
spec_file_path: str | None = None  # absolute path

# PMState
ticket_refs: list[str] = []    # created ticket IDs/URLs (empty if no integration)
# board_ref removed
```

---

## Testing summary

All tests implemented and passing (65 total after this feature):

- `test_spec_file_path_defaults_none` — state defaults
- `test_item_id_defaults_empty` — state defaults
- `test_spec_file_runs_pm` — routing
- `test_spec_file_mode_skips_issue_fetch` — intake guard
- `test_pm_reads_markdown_spec_file_and_converts` — PM spec_file path
- `test_pm_creates_tickets_via_integration_when_integration_id_set` — ticket creation
- `test_get_upload_form_returns_html` — admin GET
- `test_post_upload_saves_file_and_redirects` — admin POST
- `test_github_create_issue` / `test_plane_create_issue` / `test_jira_create_issue` — providers
- `test_md_passthrough` / `test_txt_passthrough` / `test_pdf_extraction` / `test_docx_extraction` / `test_unsupported_extension_raises` — extractor
