# Spec File Path Support

**Date:** 2026-06-12
**Status:** Implemented (diverges from original design — see notes throughout)

## Problem

`spec_ready` mode requires the spec to be embedded as JSON in the issue body. There is no way to provide a spec as a standalone file upload — the only input path is a live issue from GitHub/Jira/Plane.

## Goal

Allow an admin to upload a spec document through the admin UI and trigger a run from it, with no issue source required.

## Scope

- New `intake_mode = "spec_file"`
- Admin file upload form (`SpecUploadView` — SQLAdmin `BaseView` at `/admin/spec-upload`)
- Supported formats: `.md`, `.txt`, `.pdf`, `.docx` — all converted to Markdown before the LLM call
- PM agent converts the Markdown → `Spec` via LLM
- Intake skips issue fetch for `spec_file` mode
- `item_id` made optional (nullable) throughout
- No project required — spec may be for a new or existing project
- Optional integration selection: tickets created in the chosen tracker via `create_issue`

---

## Data Model

### `RunRecord` (`db/models.py`)

| Column | Change |
|--------|--------|
| `item_id` | `Mapped[str \| None]` — nullable; `None` for standalone spec-file runs |
| `spec_file_path` | New `Mapped[str \| None]` — **absolute path** to `runtime/uploads/<uuid>.<ext>` |

### `WorkflowState` (`graph/state.py`)

| Field | Change |
|-------|--------|
| `project` | `str = ""` — optional; standalone runs leave it empty |
| `item_id` | `str = ""` — default empty string; standalone runs leave it empty |
| `spec_file_path` | New `str \| None = None` — absolute path |
| `IntakeMode` | Gains `"spec_file"` literal |

### `PMState` (`graph/state.py`)

| Field | Note |
|-------|------|
| `ticket_refs` | `list[str]` — IDs/URLs of tickets created in the integration (empty if no integration selected) |

No `board_ref` field — the local file write is a fixed side-effect, not a tracked reference.

### `RunRequest` (`api/schemas.py`)

| Field | Change |
|-------|--------|
| `item_id` | `str \| None = None` |
| `spec_file_path` | New `str \| None = None` |

---

## File Storage

Uploaded files saved to an absolute path:

```
runtime/uploads/<uuid>.<original_ext>
```

No project prefix — the upload is project-agnostic. `spec_file_path` on `RunRecord` and `WorkflowState` stores this absolute path. The PM agent reads it directly via `Path(state.spec_file_path)`.

---

## File Extraction (`utils/file_extract.py`)

All formats are normalized to Markdown text before passing to the LLM:

| Format | Extraction |
|--------|-----------|
| `.md` / `.txt` | `path.read_text()` passthrough |
| `.pdf` | `pypdf.PdfReader` — extract text per page, join with double newline |
| `.docx` | `python-docx` — headings → `#`/`##`/`###`, bold/italic → `**`/`*`, lists → `-` |

Converting to Markdown before the LLM reduces token count by stripping binary/XML noise and normalizes all formats to one prompt path.

---

## Admin Upload (`admin/spec_upload.py`)

Implemented as a SQLAdmin **`BaseView`** (not a custom Starlette route). Mounted via `admin.add_base_view(SpecUploadView)`.

- `request.app` inside a `BaseView` is SQLAdmin's inner Starlette app, not the outer FastAPI app. The outer app (and its `runner`) is accessed via `request.app.state.outer_app`, set as `admin.admin.state.outer_app = app` in `setup_admin`.

**Form fields:**
- File input (`.md`, `.txt`, `.pdf`, `.docx`)
- "Create tickets in" dropdown — lists enabled integrations from DB (optional; "None" = no ticket creation)

**On submit:**
1. Validate extension against `SUPPORTED_EXTENSIONS`
2. Save file to `runtime/uploads/<uuid>.<ext>` (absolute path)
3. Create `RunRecord` with `intake_mode="spec_file"`, `spec_file_path=<absolute path>`, `item_id=None`, `integration_id=<selected or None>`
4. Enqueue via `Runner.start_run(intake_mode="spec_file", spec_file_path=..., integration_id=...)`
5. Redirect to `/admin/run-record/list`

No project field — the spec is not tied to a project at upload time.

---

## Runner (`graph/runner.py`)

`Runner.start_run` gains:
- `item_id: str | None = None` (was required `str`)
- `spec_file_path: str | None = None`
- `integration_id: int | None = None` (existed; now meaningful for spec_file runs)

All forwarded to `WorkflowState` construction.

---

## Intake Agent (`agents/intake.py`)

`spec_file` mode skips issue fetch entirely:

```python
if state.intake_mode == "spec_file":
    return {"intake": {"note": "spec_file mode — no issue fetch"}}
```

- `raw_issue` stays `None`
- `item_id` stays empty

---

## PM Agent (`agents/pm.py`)

PM handles both paths. When `state.intake_mode == "spec_file"`:

1. Read file via `asyncio.to_thread(_read_spec_file, Path(state.spec_file_path))`
2. `_read_spec_file` calls `to_markdown(path)` — format-aware extraction
3. Call LLM with a Markdown-to-spec system prompt → `.with_structured_output(Spec)`
4. Write spec to local `RUNTIME_DIR/board/` (fixed side-effect, not configurable)
5. If `state.integration_id` is set: call `provider.create_issue(ticket.title, body)` for each ticket in the spec; return `ticket_refs`

New system prompt variant (`_SPEC_FILE_SYSTEM`) instructs the LLM to extract and structure an existing spec document rather than generate one from scratch.

No `board_ref` is returned — the local file write path is not surfaced in the state.

---

## IssueProvider (`integrations/base.py`)

New method added to the protocol:

```python
async def create_issue(self, title: str, body: str) -> str: ...
```

Implemented in all three providers (GitHub, Plane, Jira). The PM agent calls this for each `Ticket` in the spec when `integration_id` is set.

---

## Graph Routing (`graph/builder.py`)

```python
def _route_after_intake(state: WorkflowState) -> str:
    if state.intake_mode in ("raw_to_spec", "spec_file"):
        return "pm"
    return "research"
```

`spec_file` runs go through PM (file→Spec conversion) then continue normally.

---

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| File not found at path | PM sets `pm.error`, run marked `failed` at merge |
| Unsupported file extension | Upload view rejects with 400 before the run starts |
| LLM fails to produce valid `Spec` | `generate()` raises; caught by node wrapper, sets `pm.error` |
| `spec_file_path` is `None` in `spec_file` mode | PM sets `pm.error`: "spec_file mode but no spec_file_path on state" |
| `integration_id` set but integration not found | `provider_for` raises `LookupError`; caught by node wrapper |

---

## Testing

- Unit: `IntakeAgent` with `spec_file` mode returns no `raw_issue`
- Unit: `PMAgent` with `spec_file` mode reads file and calls LLM (mock LLM + FakeBoard)
- Unit: `PMAgent` with `integration_id` set creates tickets via provider
- Unit: `_route_after_intake` returns `"pm"` for both `raw_to_spec` and `spec_file`
- Unit: `to_markdown` for all four formats (md, txt, pdf, docx)
- Admin: form renders (GET), file saved to correct path, `RunRecord` created (POST)
- Providers: `create_issue` for GitHub, Plane, Jira (httpx MockTransport)
