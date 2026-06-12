# How to add a connector (GitHub / Jira / Plane)

A **connector** is a saved, credentialed connection to an external system, stored once in the
`connectors` table (secret **encrypted at rest**). A connector can be used as an issue **source**
(`is_source` — PM reads issues from it), a ticket **sink** (`is_sink` — PM creates tickets in it),
or **both** — so e.g. Jira is configured a single time instead of twice. The default sink (used when
a run doesn't pick one) is the connector flagged `is_default_sink`.

Add/edit connectors in the admin portal (`/admin` → **Connectors**); a run then references one as
its source (`integration_id`) and/or sink (`task_sink_id`).

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
2. Open the **admin portal** → <http://127.0.0.1:8000/admin> → **Connectors** → **New**.
3. Fill in the fields:

   | Field       | Value                                              | Notes |
   |-------------|----------------------------------------------------|-------|
   | `name`      | `plane-github` (any label)                         | unique, your choice |
   | `kind`      | `github`                                           | |
   | `is_source` | ✓                                                  | use it to read issues |
   | `base_url`  | *(blank)*                                          | only set for **GitHub Enterprise**, e.g. `https://github.mycorp.com/api/v3` |
   | `config`    | `{"repo": "makeplane/plane"}`                      | the `owner/name` repo to read issues from |
   | `secret`    | your PAT (e.g. `ghp_…`)                            | stored encrypted; never shown in lists |
   | `enabled`   | ✓                                                  | |

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

## Access via an MCP server (instead of the built-in client)

Each connector can reach its system either through our **built-in httpx client** (default) or via
the system's own **hosted MCP server**, whose tools (`get_issue`, `create_issue`,
`create_pull_request`, …) are loaded with `langchain-mcp-adapters` and handed to the agents.

Set this on the connector (admin → **Connectors**):

| Field       | Value | Notes |
|-------------|-------|-------|
| `transport` | `http` | switches this connector from the built-in client to a **hosted MCP** server |
| `base_url`  | the MCP server URL | e.g. GitHub's hosted MCP, Atlassian's remote MCP |
| `secret`    | API token / OAuth token | sent as `Authorization: Bearer <secret>` by default |
| `config`    | `{"headers": {...}}` | optional extra/override headers (e.g. a different auth header) |

Leave `transport` blank to keep the built-in client. Only **remote/hosted HTTP** MCP is wired (no
local `uvx`/`npx` servers in the image).

> **Status:** the MCP **tool-loading** layer is in place and tested (`mcp_tools_for(connector_id)`),
> and the `create_agent` runtime can call MCP tools. **Binding a connector's MCP tools into the live
> agent loop** (so PM/Coding/Reviewer act through MCP) lands with the agent-tools phase (P2) and is
> verified against a real server. See `docs/plan/agent_runtime_and_connectors_plan.md` §5.

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

After PM produces a spec, it breaks it into tickets and **pushes them to a task sink**. A sink is
just a **connector with `is_sink` ticked** (same `/admin` → **Connectors** list — a Jira connector
can be both a source and a sink). Sink-relevant fields:

| Field | Value | Notes |
|---|---|---|
| `name` | any label | |
| `kind` | `file` / `jira` / `plane` / `sheets` | `file` = local board (default); `sheets` later |
| `is_sink` | ✓ | marks the connector usable as a ticket destination |
| `is_default_sink` | ✓ on one | used when a run doesn't pick a sink (or runs autonomously) |
| `base_url` | site URL | required for Jira; Plane defaults to cloud |
| `config` | `{"project_key": "ENG", "email": "...", "issue_type": "Task"}` (Jira) / `{"workspace_slug": "...", "project_id": "..."}` (Plane) | `issue_type` defaults to `Task` |
| `secret` | API token | encrypted |

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

To script connector creation (e.g. a seed task) instead of the portal:

```python
from ash.db.base import get_sessionmaker
from ash.db.models import ConnectorKind
from ash.integrations.service import create_connector

async def seed():
    async with get_sessionmaker()() as session:
        # one Jira connector used as BOTH a source and the default sink
        await create_connector(
            session,
            name="acme-jira",
            kind=ConnectorKind.jira,
            secret="jira-api-token",
            config={"email": "you@acme.com", "project_key": "ENG", "issue_type": "Task"},
            base_url="https://acme.atlassian.net",
            is_source=True,
            is_sink=True,
            is_default_sink=True,
        )
```

(`SECRET_KEY` must be set so the token is encrypted on write.)

---

## How it maps internally

A run references a connector as its source (`integration_id`) and/or sink (`task_sink_id`).
- **Source:** `integrations.service.provider_for(id)` loads the connector (must have `is_source`),
  decrypts the secret, and `registry.build_provider()` returns the matching `IssueProvider`
  (`GitHub` / `Jira` / `Plane`); intake calls `provider.fetch_issue(item_id)` → a `RawIssue`.
- **Sink:** `sinks.service.resolve_task_sink(...)` picks the connector (explicit → default → file
  board) and `build_sink()` returns the matching `TicketSink`; PM calls `sink.publish(spec)`.

**Adding a new system** = a new `ConnectorKind` + a provider and/or sink backend — no changes to the
agents or the graph.

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
