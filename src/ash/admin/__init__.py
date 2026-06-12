"""SQLAdmin portal mounted at /admin (CRUD over integrations + run registry)."""

from __future__ import annotations

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy.ext.asyncio import AsyncEngine

import ash.admin._compat  # noqa: F401 — applies the sqladmin/wtforms boolean-widget shim on import
from ash.admin.auth import AdminAuth
from ash.admin.views import AdminUserAdmin, ConnectorAdmin, RunRecordAdmin
from ash.config.settings import Settings


def setup_admin(app: FastAPI, engine: AsyncEngine, settings: Settings) -> Admin:
    auth = AdminAuth(
        secret_key=settings.secret_key or "dev-insecure-session-key",
        username=settings.admin_user,
        password=settings.admin_password,
    )
    admin = Admin(app, engine, authentication_backend=auth, title="ASH Admin")
    admin.add_view(ConnectorAdmin)
    admin.add_view(RunRecordAdmin)
    admin.add_view(AdminUserAdmin)
    return admin
