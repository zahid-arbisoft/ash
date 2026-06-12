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
        Connector.is_source,
        Connector.is_sink,
        Connector.is_default_sink,
        Connector.enabled,
    ]
    # secret is intentionally excluded from list views; it is encrypted at rest
    form_columns = [
        Connector.name,
        Connector.kind,
        Connector.base_url,
        Connector.config,
        Connector.secret,
        Connector.is_source,
        Connector.is_sink,
        Connector.is_default_sink,
        Connector.enabled,
    ]
    column_searchable_list = [Connector.name]


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
