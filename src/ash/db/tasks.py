"""CRUD helpers for AgentTask and AgentPolicyRecord.

All functions accept an AsyncSession and return ORM objects. Callers are responsible for
committing the session; helpers call session.flush() so IDs are available before commit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.config.settings import AgentPolicy, ProjectConfig
from ash.db.models import AgentPolicyRecord, AgentTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# AgentTask CRUD
# ---------------------------------------------------------------------------


async def create_agent_task(
    session: AsyncSession,
    *,
    agent_name: str,
    project: str,
    run_id: str,
    item_id: str,
    ticket_id: str = "",
    title: str = "",
    max_retries: int = 0,
    status: str = "pending",
) -> AgentTask:
    task = AgentTask(
        agent_name=agent_name,
        project=project,
        run_id=run_id,
        ticket_id=ticket_id,
        item_id=item_id,
        title=title,
        status=status,
        retry_count=0,
        max_retries=max_retries,
    )
    session.add(task)
    await session.flush()
    return task


async def get_task_for_run(
    session: AsyncSession, run_id: str, agent_name: str, ticket_id: str = ""
) -> AgentTask | None:
    """Fetch the task for (run, agent, story). Build-team tasks are per-story (ticket_id set);
    run-level tasks (intake/pm/rfc) use ticket_id=""."""
    result = await session.execute(
        select(AgentTask).where(
            AgentTask.run_id == run_id,
            AgentTask.agent_name == agent_name,
            AgentTask.ticket_id == ticket_id,
        )
    )
    return result.scalar_one_or_none()


async def upsert_agent_task(
    session: AsyncSession,
    *,
    agent_name: str,
    project: str,
    run_id: str,
    item_id: str,
    ticket_id: str = "",
    title: str = "",
    max_retries: int = 0,
    status: str = "pending",
) -> AgentTask:
    """Create the task if absent; return existing if already present (idempotent)."""
    existing = await get_task_for_run(session, run_id, agent_name, ticket_id)
    if existing is not None:
        return existing
    return await create_agent_task(
        session,
        agent_name=agent_name,
        project=project,
        run_id=run_id,
        item_id=item_id,
        ticket_id=ticket_id,
        title=title,
        max_retries=max_retries,
        status=status,
    )


async def update_task_status(
    session: AsyncSession,
    task_id: int,
    status: str,
    **fields: Any,
) -> AgentTask | None:
    task = await session.get(AgentTask, task_id)
    if task is None:
        return None
    task.status = status
    now = _utcnow()
    if status == "in_progress" and task.started_at is None:
        task.started_at = now
    if status in ("completed", "failed", "cancelled"):
        task.completed_at = now
    for k, v in fields.items():
        setattr(task, k, v)
    await session.flush()
    return task


async def mark_task_started(session: AsyncSession, task_id: int) -> AgentTask | None:
    return await update_task_status(session, task_id, "in_progress", started_at=_utcnow())


async def mark_task_completed(
    session: AsyncSession, task_id: int, result_ref: str | None = None
) -> AgentTask | None:
    return await update_task_status(
        session, task_id, "completed", result_ref=result_ref, completed_at=_utcnow()
    )


async def mark_task_failed(
    session: AsyncSession, task_id: int, error: str, *, retry: bool = False
) -> AgentTask | None:
    task = await session.get(AgentTask, task_id)
    if task is None:
        return None
    task.error = error
    if retry and task.retry_count < task.max_retries:
        task.retry_count += 1
        task.status = "pending"
        task.started_at = None
    else:
        task.status = "failed"
        task.completed_at = _utcnow()
    await session.flush()
    return task


async def list_tasks(
    session: AsyncSession,
    agent_name: str,
    project: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentTask]:
    q = select(AgentTask).where(
        AgentTask.agent_name == agent_name, AgentTask.project == project
    )
    if status:
        q = q.where(AgentTask.status == status)
    q = q.order_by(AgentTask.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars())


async def list_tasks_for_run(session: AsyncSession, run_id: str) -> list[AgentTask]:
    result = await session.execute(
        select(AgentTask)
        .where(AgentTask.run_id == run_id)
        .order_by(AgentTask.created_at)
    )
    return list(result.scalars())


async def get_all_pending_tasks(
    session: AsyncSession, limit: int = 200
) -> list[AgentTask]:
    """Return pending tasks across all agents and projects, ordered oldest-first."""
    result = await session.execute(
        select(AgentTask)
        .where(AgentTask.status == "pending")
        .order_by(AgentTask.created_at)
        .limit(limit)
    )
    return list(result.scalars())


async def get_pending_tasks(
    session: AsyncSession, agent_name: str, project: str, limit: int
) -> list[AgentTask]:
    result = await session.execute(
        select(AgentTask)
        .where(
            AgentTask.agent_name == agent_name,
            AgentTask.project == project,
            AgentTask.status == "pending",
        )
        .order_by(AgentTask.created_at)
        .limit(limit)
    )
    return list(result.scalars())


async def active_task_count(session: AsyncSession, agent_name: str, project: str) -> int:
    result = await session.execute(
        select(func.count()).where(
            AgentTask.agent_name == agent_name,
            AgentTask.project == project,
            AgentTask.status == "in_progress",
        )
    )
    return result.scalar_one()


async def today_completed_count(session: AsyncSession, agent_name: str, project: str) -> int:
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.count()).where(
            AgentTask.agent_name == agent_name,
            AgentTask.project == project,
            AgentTask.status == "completed",
            AgentTask.completed_at >= today_start,
        )
    )
    return result.scalar_one()


async def task_stats(
    session: AsyncSession, agent_name: str, project: str
) -> dict[str, int]:
    """Return counts per status plus a success_rate (0-100)."""
    rows = await session.execute(
        select(AgentTask.status, func.count())
        .where(AgentTask.agent_name == agent_name, AgentTask.project == project)
        .group_by(AgentTask.status)
    )
    counts: dict[str, int] = dict(rows.all())  # type: ignore[arg-type]
    done = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    total = done + failed
    counts["success_rate"] = int(done * 100 / total) if total else 0
    return counts


# ---------------------------------------------------------------------------
# AgentPolicyRecord CRUD
# ---------------------------------------------------------------------------


async def get_policy_override(
    session: AsyncSession, project: str, agent_name: str
) -> AgentPolicyRecord | None:
    result = await session.execute(
        select(AgentPolicyRecord).where(
            AgentPolicyRecord.project == project,
            AgentPolicyRecord.agent_name == agent_name,
        )
    )
    return result.scalar_one_or_none()


async def upsert_policy_override(
    session: AsyncSession,
    project: str,
    agent_name: str,
    **fields: Any,
) -> AgentPolicyRecord:
    record = await get_policy_override(session, project, agent_name)
    if record is None:
        record = AgentPolicyRecord(project=project, agent_name=agent_name)
        session.add(record)
    for k, v in fields.items():
        setattr(record, k, v)
    await session.flush()
    return record


async def delete_policy_override(
    session: AsyncSession, project: str, agent_name: str
) -> None:
    record = await get_policy_override(session, project, agent_name)
    if record is not None:
        await session.delete(record)
        await session.flush()


async def resolve_policy(
    session: AsyncSession, project_config: ProjectConfig, agent_name: str
) -> AgentPolicy:
    """Merge DB override > YAML > code default into one AgentPolicy."""
    yaml_policy = project_config.agent_policy(agent_name)
    db_record = await get_policy_override(session, project_config.name, agent_name)
    if db_record is None:
        return yaml_policy
    # DB wins on every field that the record explicitly sets.
    return AgentPolicy(
        trigger=db_record.trigger,  # type: ignore[arg-type]
        enabled=db_record.enabled,
        concurrency_limit=db_record.concurrency_limit,
        daily_quota=db_record.daily_quota,
        max_retries=db_record.max_retries,
        schedule_cron=db_record.schedule_cron,
    )
