"""SQLAdmin model views for the app tables."""

from __future__ import annotations

from sqladmin import ModelView

from ash.db.models import AdminUser, Integration, RunRecord


class IntegrationAdmin(ModelView, model=Integration):
    name = "Integration"
    name_plural = "Integrations"
    icon = "fa-solid fa-plug"
    column_list = [
        Integration.id,
        Integration.name,
        Integration.kind,
        Integration.enabled,
        Integration.created_at,
    ]
    # secret is intentionally excluded from list views; it is encrypted at rest
    form_columns = [
        Integration.name,
        Integration.kind,
        Integration.base_url,
        Integration.config,
        Integration.secret,
        Integration.enabled,
    ]
    column_searchable_list = [Integration.name]


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
