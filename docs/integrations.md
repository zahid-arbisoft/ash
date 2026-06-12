# How to add an integration (GitHub / Jira / Plane)

An **integration** is a saved, credentialed connection to an *issue source*. ASH pulls a ticket
from it (the intake step), then the agent graph turns that ticket into a spec and/or code. Each
integration is a row in the `integrations` table; its secret (API token) is **encrypted at rest**.

Adding an integration = creating that row (via the admin portal, the API/UI run picker, or code) so
runs can reference it by `integration_id`.

---

## Prerequisites (one-time)

1. **Postgres up** and `POSTGRES_DSN` set (the integrations table lives here):
   ```bash
   just db-up
   ```
2. **`SECRET_KEY` set** in `.env` — a Fernet key used to encrypt integration tokens. Without it the
   app refuses to read/write secrets. Generate one:
   ```bash
   just docker-secret-key
   ```
3. **An admin login** for the portal (`/admin`). Either the env bootstrap user
   (`ADMIN_USER` / `ADMIN_PASSWORD`) or a DB user:
   ```bash
   just create-admin alice      # prompts for the password
   ```
4. Start the app: `just serve` → open <http://127.0.0.1:8000>.

---

## Add a GitHub integration (step by step)

1. Create a **GitHub Personal Access Token** (PAT) at
   <https://github.com/settings/tokens>:
   - **Public repos, read-only:** a token isn't strictly required, but one lifts rate limits.
     A classic token with **no scopes** (or fine-grained *Issues: read*) is enough to read issues.
   - **Private repos:** classic token with the **`repo`** scope (or a fine-grained token granting
     *Issues: Read* on the repo).
   - **Posting comments back** (deferred feature, but scope it now if you want it later): *Issues:
     Read and write* / classic `repo`.
2. Open the **admin portal** → <http://127.0.0.1:8000/admin> → **Integrations** → **New**.
3. Fill in the fields:

   | Field      | Value                                              | Notes |
   |------------|----------------------------------------------------|-------|
   | `name`     | `plane-github` (any label)                         | unique, your choice |
   | `kind`     | `github`                                           | |
   | `base_url` | *(blank)*                                          | only set for **GitHub Enterprise**, e.g. `https://github.mycorp.com/api/v3` |
   | `config`   | `{"repo": "makeplane/plane"}`                      | the `owner/name` repo to read issues from |
   | `secret`   | your PAT (e.g. `ghp_…`)                            | stored encrypted; never shown in lists |
   | `enabled`  | ✓                                                  | |

4. **Save.** That's it — the integration is live.

### Use it

- **UI:** <http://127.0.0.1:8000/ui/runs/new> → pick the integration, enter an **item id** (the
  GitHub **issue number**, e.g. `9213`), choose an intake mode, **Start run**.
- **API:**
  ```bash
  curl -X POST localhost:8000/runs -H 'content-type: application/json' \
    -d '{"project":"plane","item_id":"9213","integration_id":1,"intake_mode":"raw_to_spec"}'
  ```
  (`integration_id` is the row id shown in the admin list.)

> **Legacy shortcut:** if you omit `integration_id`, intake falls back to a GitHub source derived
> from the project's `projects/<name>.yaml` `issues.source_repo` + the `GITHUB_TOKEN` env var. The
> integration row is the preferred, multi-source path.

---

## Add a Jira integration

1. Create a Jira **API token** at <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. Admin → Integrations → New:

   | Field      | Value | Notes |
   |------------|-------|-------|
   | `name`     | `acme-jira` | |
   | `kind`     | `jira` | |
   | `base_url` | `https://your-domain.atlassian.net` | **required** (your site) |
   | `config`   | `{"email": "you@acme.com", "project_key": "ENG"}` | auth email + project |
   | `secret`   | your Jira API token | used with the email as HTTP Basic auth |
   | `enabled`  | ✓ | |

3. **Item id** = the Jira **issue key**, e.g. `ENG-123`.

---

## Add a Plane integration

1. Create a Plane **API key** (workspace settings → API tokens).
2. Admin → Integrations → New:

   | Field      | Value | Notes |
   |------------|-------|-------|
   | `name`     | `acme-plane` | |
   | `kind`     | `plane` | |
   | `base_url` | *(blank for Plane Cloud)* or your self-hosted URL | defaults to `https://api.plane.so` |
   | `config`   | `{"workspace_slug": "acme", "project_id": "<uuid>"}` | from the project URL |
   | `secret`   | your Plane API key | sent as the `X-API-Key` header |
   | `enabled`  | ✓ | |

3. **Item id** = the Plane **issue id**.

---

## Intake modes (what happens to the ticket)

Chosen per run (UI dropdown or `intake_mode` in the API):

| Mode          | Meaning |
|---------------|---------|
| `raw_to_spec` | **(default)** PM converts the raw issue → a structured spec → Board, then the build team runs. |
| `spec_ready`  | The issue **body already is a spec** (JSON matching the `Spec` schema). PM is skipped. |
| `raw_to_dev`  | Feed the raw issue **straight to the build team** (Research → Coding). PM is skipped. |

---

## Task sinks — where PM pushes the generated tickets

After PM produces a spec, it breaks it into tickets and **pushes them to a task sink**. Sinks are
DB rows managed in the admin portal (`/admin` → **Task sinks**), with secrets encrypted at rest:

| Field | Value | Notes |
|---|---|---|
| `name` | any label | |
| `kind` | `file` / `jira` / `plane` / `sheets` | `file` = local board (default); `sheets` later |
| `base_url` | site URL | required for Jira; Plane defaults to cloud |
| `config` | `{"project_key": "ENG", "email": "..."}` (Jira) / `{"workspace_slug": "...", "project_id": "..."}` (Plane) | |
| `secret` | API token | encrypted |
| `is_default` | ✓ on one row | used when a run doesn't pick a sink (or runs autonomously) |

**Selection per run:** explicit choice (UI dropdown / `task_sink_id` in the API) → else the
**admin default** → else the **local file board** (`runtime/<project>/board/`).

## Uploading spec files (PM reads them)

PM can build a spec from **uploaded documents** (`pdf` / `docx` / `md` / `txt` / `html`), not just
issue text:

- **UI:** the start-run form (`/ui/runs/new`) has a file picker — leave *item id* blank for an
  attachments-only run.
- **API:** upload first, then start the run with the returned paths:
  ```bash
  curl -F 'files=@spec.pdf' localhost:8000/uploads        # -> {"paths": ["/app/runtime/uploads/…/spec.pdf"]}
  curl -X POST localhost:8000/runs -H 'content-type: application/json' \
    -d '{"project":"plane","item_id":"upload","attachments":["/app/runtime/uploads/…/spec.pdf"],"task_sink_id":1}'
  ```

**Spikes:** PM marks any ticket needing investigation as a **spike** (`type: spike`,
`needs_research: true`); the Research agent picks those up from the spec.

---

## Programmatic / seeding alternative

To script integration creation (e.g. a seed task) instead of the portal:

```python
from ash.db.base import get_sessionmaker
from ash.db.models import ProviderKind
from ash.integrations.service import create_integration

async def seed():
    async with get_sessionmaker()() as session:
        await create_integration(
            session,
            name="plane-github",
            kind=ProviderKind.github,
            secret="ghp_…",
            config={"repo": "makeplane/plane"},
        )
```

(`SECRET_KEY` must be set so the token is encrypted on write.)

---

## How it maps internally

`integration_id` on a run → `integrations.service.provider_for()` loads the row, decrypts the
secret, and `registry.build_provider()` returns the matching `IssueProvider`
(`GitHubIssueProvider` / `JiraIssueProvider` / `PlaneIssueProvider`). The intake agent calls
`provider.fetch_issue(item_id)` → a normalized `RawIssue`. **Adding a brand-new source type** means
adding a provider + a `ProviderKind` value — no changes to the agents or the graph.

---

## Security notes

- Tokens are encrypted at rest with Fernet (`SECRET_KEY`); they are excluded from admin list views
  and never logged.
- Rotate the token by editing the integration's `secret` and saving; rotating `SECRET_KEY` itself
  invalidates all stored secrets (re-enter them).
- Scope tokens to the **minimum** needed (read-only unless you enable comment-back).

## Troubleshooting

- **401/403 fetching an issue:** token missing/expired or lacks scope for a private repo/project.
- **404:** wrong `config` (`repo` / `project_key` / `workspace_slug`+`project_id`) or wrong item id.
- **"Settings.secret_key is unset":** set `SECRET_KEY` in `.env` and restart.
- **Jira "requires base_url":** the `base_url` field is mandatory for Jira.
