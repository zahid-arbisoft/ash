"""Admin authentication backend.

Checks DB-backed `AdminUser` rows (PBKDF2-hashed); falls back to the env bootstrap user
(`ADMIN_USER`/`ADMIN_PASSWORD`) so you can log in before creating any users.
"""

from __future__ import annotations

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from ash.admin.users import authenticate
from ash.db.base import get_sessionmaker


class AdminAuth(AuthenticationBackend):
    def __init__(self, *, secret_key: str, username: str, password: str) -> None:
        super().__init__(secret_key=secret_key)
        self._env_user = username
        self._env_password = password

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        if await self._check(username, password):
            request.session.update({"token": "ok"})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("token") == "ok"

    async def _check(self, username: str, password: str) -> bool:
        if not password:
            return False
        # env bootstrap user
        if self._env_password and username == self._env_user and password == self._env_password:
            return True
        # DB users (best-effort: never let a DB hiccup block the bootstrap login)
        try:
            async with get_sessionmaker()() as session:
                return await authenticate(session, username, password)
        except Exception:  # noqa: BLE001
            return False
