"""SQLAdmin model views for the app tables."""

from __future__ import annotations

from sqladmin import ModelView

from ash.db.models import AdminUser, Connector, RunRecord


class ConnectorAdmin(ModelView, model=Connector):
    name = "Connector"
    name_plural = "Connectors"
    icon = "fa-solid fa-plug"
    column_list = [
        Connector.id,
        Connector.name,
        Connector.kind,
        Connector.transport,
        Connector.is_source,
        Connector.is_sink,
        Connector.is_default_sink,
        Connector.enabled,
    ]
    # secret is intentionally excluded from list views; it is encrypted at rest
    form_columns = [
        Connector.name,
        Connector.kind,
        Connector.transport,
        Connector.base_url,
        Connector.config,
        Connector.secret,
        Connector.is_source,
        Connector.is_sink,
        Connector.is_default_sink,
        Connector.enabled,
    ]
    column_searchable_list = [Connector.name]
    column_descriptions = {
        "config": (
            'Kind-specific JSON.  '
            'github: {"repo": "owner/name"}  |  '
            'jira: {"email": "you@example.com", "project_key": "ENG"}  |  '
            'plane: {"workspace_slug": "acme", "project_id": "uuid"}  '
            '— full field reference at /ui/connectors'
        ),
        "secret": (
            "API token / password (encrypted at rest).  "
            "github: Personal Access Token (PAT, repo scope)  |  "
            "jira: API token from id.atlassian.com  |  "
            "plane: API key from workspace Settings → API Tokens"
        ),
        "base_url": (
            "API endpoint override.  "
            "github: leave blank (github.com) or GitHub Enterprise URL  |  "
            "jira: required — e.g. https://your-domain.atlassian.net  |  "
            "plane: leave blank (Plane Cloud) or self-hosted URL"
        ),
        "transport": (
            "Leave blank to use the built-in HTTP client.  "
            "Set to 'http' only for connectors that expose a hosted MCP server."
        ),
        "is_source": "Allow this connector to be selected as an issue source when starting a run.",
        "is_sink": "Allow this connector to receive tickets created by the PM agent.",
        "is_default_sink": "Automatically use this connector as the ticket sink for all new runs.",
    }


class RunRecordAdmin(ModelView, model=RunRecord):
    name = "Run"
    name_plural = "Runs"
    icon = "fa-solid fa-list-check"
    can_create = False
    can_edit = False
    column_list = [
        RunRecord.run_id,
        RunRecord.project,
        RunRecord.item_id,
        RunRecord.intake_mode,
        RunRecord.created_at,
    ]
    column_default_sort = [(RunRecord.created_at, True)]


class AdminUserAdmin(ModelView, model=AdminUser):
    name = "Admin user"
    name_plural = "Admin users"
    icon = "fa-solid fa-user-shield"
    # read-only: create/reset passwords via `just create-admin` so they are always hashed
    can_create = False
    can_edit = False
    can_delete = True
    column_list = [AdminUser.id, AdminUser.username, AdminUser.created_at]
