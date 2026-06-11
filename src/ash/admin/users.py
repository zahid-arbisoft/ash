"""CRUD for admin-portal users."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.admin.security import hash_password, verify_password
from ash.db.models import AdminUser


async def get_admin_user(session: AsyncSession, username: str) -> AdminUser | None:
    result = await session.execute(select(AdminUser).where(AdminUser.username == username))
    return result.scalar_one_or_none()


async def list_admin_users(session: AsyncSession) -> list[AdminUser]:
    result = await session.execute(select(AdminUser).order_by(AdminUser.username))
    return list(result.scalars().all())


async def create_or_update_admin(
    session: AsyncSession, *, username: str, password: str
) -> AdminUser:
    """Create the admin user, or reset its password if it already exists (idempotent)."""
    user = await get_admin_user(session, username)
    if user is None:
        user = AdminUser(username=username, password_hash=hash_password(password))
        session.add(user)
    else:
        user.password_hash = hash_password(password)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, username: str, password: str) -> bool:
    user = await get_admin_user(session, username)
    return user is not None and verify_password(password, user.password_hash)
