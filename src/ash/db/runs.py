"""Run/spec persistence helpers (plan §10.1, A0).

These keep a denormalized copy of run status and the generated spec in the app DB so the UI can
list and search PM runs without replaying the LangGraph checkpointer (which stays the source of
truth for live state). All writers are **best-effort**: callers wrap them so a DB hiccup never
fails an agent run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.base import get_sessionmaker
from ash.db.models import RunRecord, SpecRecord, StoryRecord

if TYPE_CHECKING:
    from ash.schemas import Spec


async def persist_spec_record(
    *,
    run_id: str,
    project: str,
    item_id: str,
    intake_mode: str,
    spec: Spec,
    board_ref: str | None = None,
    ticket_refs: list[str] | None = None,
) -> None:
    """Upsert the spec for a run (one SpecRecord per run_id)."""
    spikes = sum(1 for t in spec.tickets if t.needs_research)
    async with get_sessionmaker()() as session:
        rec = await session.get(SpecRecord, run_id)
        if rec is None:
            rec = SpecRecord(run_id=run_id)
            session.add(rec)
        rec.project = project
        rec.item_id = item_id
        rec.intake_mode = intake_mode
        rec.epic_title = spec.epic.title[:500]
        rec.summary = spec.epic.summary
        rec.ticket_count = len(spec.tickets)
        rec.spike_count = spikes
        rec.spec_json = spec.model_dump(mode="json")
        rec.board_ref = board_ref
        if ticket_refs is not None:
            rec.ticket_refs = ticket_refs
        await session.commit()


async def update_spec_ticket_refs(run_id: str, ticket_refs: list[str]) -> None:
    """Record where a run's tickets were pushed, after the publish gate."""
    async with get_sessionmaker()() as session:
        rec = await session.get(SpecRecord, run_id)
        if rec is not None:
            rec.ticket_refs = ticket_refs
            await session.commit()


async def update_run_status(run_id: str, status: str) -> None:
    """Best-effort denormalized status copy on the RunRecord (badge in the runs list)."""
    async with get_sessionmaker()() as session:
        rec = await session.get(RunRecord, run_id)
        if rec is not None:
            rec.status = status
            await session.commit()


async def search_spec_records(
    session: AsyncSession,
    *,
    query: str = "",
    project: str = "",
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[SpecRecord], int]:
    """Paginated, text-searchable PM-runs listing (epic title / summary / item id)."""
    from sqlalchemy import func

    stmt = select(SpecRecord).order_by(SpecRecord.created_at.desc())
    if project:
        stmt = stmt.where(SpecRecord.project == project)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                SpecRecord.epic_title.ilike(like),
                SpecRecord.summary.ilike(like),
                SpecRecord.item_id.ilike(like),
            )
        )
    total: int = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = list(
        (await session.execute(stmt.limit(per_page).offset((page - 1) * per_page))).scalars().all()
    )
    return rows, total


async def list_workbench_runs(
    session: AsyncSession,
    *,
    query: str = "",
    project: str = "",
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated listing of PM workbench runs (RunRecord.pm_only=True), newest first.

    Sources from RunRecord — not SpecRecord — so runs still generating (no spec yet) are included.
    Each row is enriched with its SpecRecord summary (epic_title / ticket_count) via one IN query,
    so rows without a spec yet fall back to the item id in the template.
    """
    from sqlalchemy import func

    stmt = (
        select(RunRecord)
        .where(RunRecord.pm_only.is_(True))
        .order_by(RunRecord.created_at.desc())
    )
    if project:
        stmt = stmt.where(RunRecord.project == project)
    if query:
        stmt = stmt.where(RunRecord.item_id.ilike(f"%{query}%"))
    total: int = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    runs = list(
        (await session.execute(stmt.limit(per_page).offset((page - 1) * per_page))).scalars().all()
    )
    # Enrich with spec summaries in one query over this page's run_ids.
    specs: dict[str, SpecRecord] = {}
    if runs:
        spec_rows = await session.execute(
            select(SpecRecord).where(SpecRecord.run_id.in_([r.run_id for r in runs]))
        )
        specs = {s.run_id: s for s in spec_rows.scalars().all()}
    rows: list[dict[str, Any]] = []
    for r in runs:
        spec = specs.get(r.run_id)
        rows.append(
            {
                "run": r,
                "epic_title": spec.epic_title if spec else None,
                "ticket_count": spec.ticket_count if spec else None,
            }
        )
    return rows, total


async def list_dev_runs(
    session: AsyncSession,
    *,
    query: str = "",
    project: str = "",
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Paginated listing of runs that have a PM spec (so there are stories to build), newest first.

    Powers the Dev workbench list. Sources from SpecRecord (a spec exists → buildable stories),
    joined to the RunRecord for status/mode. Each row is enriched with story progress
    (done/total) from StoryRecord so the list shows build state at a glance.
    """
    from sqlalchemy import func

    stmt = (
        select(SpecRecord, RunRecord)
        .join(RunRecord, RunRecord.run_id == SpecRecord.run_id)
        .order_by(SpecRecord.created_at.desc())
    )
    if project:
        stmt = stmt.where(SpecRecord.project == project)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                SpecRecord.epic_title.ilike(like),
                SpecRecord.summary.ilike(like),
                SpecRecord.item_id.ilike(like),
            )
        )
    total: int = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    pairs = list(
        (await session.execute(stmt.limit(per_page).offset((page - 1) * per_page))).all()
    )
    # Enrich with per-run story progress (done/total) in one grouped query.
    run_ids = [rec.run_id for rec, _run in pairs]
    progress: dict[str, dict[str, int]] = {}
    if run_ids:
        story_rows = await session.execute(
            select(
                StoryRecord.run_id,
                func.count().label("total"),
                func.sum(case((StoryRecord.status == "completed", 1), else_=0)).label("done"),
            )
            .where(StoryRecord.run_id.in_(run_ids))
            .group_by(StoryRecord.run_id)
        )
        for rid, tot, done in story_rows.all():
            progress[rid] = {"total": int(tot or 0), "done": int(done or 0)}
    rows: list[dict[str, Any]] = []
    for rec, run in pairs:
        prog = progress.get(rec.run_id, {"total": 0, "done": 0})
        rows.append(
            {
                "run": run,
                "epic_title": rec.epic_title,
                "ticket_count": rec.ticket_count,
                "stories_total": prog["total"],
                "stories_done": prog["done"],
            }
        )
    return rows, total
