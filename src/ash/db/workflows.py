"""Workflow CRUD + snapshot/resolution helpers (workflow-builder change).

A workflow is a reusable, named agent flow. In v1 (OD1: subset + trigger only) it controls each
agent's `enabled` + `trigger`; execution stays in the canonical pipeline order. A run snapshots the
workflow at start (so edits never change past/in-flight runs), and `BaseAgent._resolve_policy`
layers the snapshot between the DB override and YAML (DB > workflow > YAML > code default).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.db.models import Workflow

# Gateable agents in canonical pipeline order (intake is a no-LLM fetch step and isn't configurable).
WORKFLOW_AGENTS: tuple[str, ...] = ("pm", "rfc", "research", "dev", "reviewer", "fixer")
STORY_EXECUTIONS: tuple[str, ...] = ("all", "selected", "one_by_one")


def default_steps() -> list[dict[str, Any]]:
    """The built-in default flow: every agent enabled + manual (matches the engine default — every
    run is cockpit-driven, decision #33). Used when no workflow is selected."""
    return [{"agent": a, "trigger": "manual", "enabled": True} for a in WORKFLOW_AGENTS]


def normalize_steps(raw: Any) -> list[dict[str, Any]]:
    """Coerce arbitrary input into a clean, canonical-ordered step list.

    - Keeps only known agents; fills any missing agent with a disabled step (so the flow always
      describes every agent explicitly).
    - Forces `trigger` to auto|manual and `enabled` to bool.
    - Reorders to the canonical pipeline order (v1 ignores authored order for execution, but we
      store canonical so resolution is unambiguous; the builder may present a draggable list).
    """
    by_agent: dict[str, dict[str, Any]] = {}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", "")).strip()
        if agent not in WORKFLOW_AGENTS or agent in by_agent:
            continue
        trigger = "auto" if str(item.get("trigger", "manual")) == "auto" else "manual"
        enabled = bool(item.get("enabled", True))
        by_agent[agent] = {"agent": agent, "trigger": trigger, "enabled": enabled}
    return [
        by_agent.get(a, {"agent": a, "trigger": "manual", "enabled": True})
        for a in WORKFLOW_AGENTS
    ]


def snapshot_for(wf: Workflow) -> dict[str, Any]:
    """An immutable snapshot of a workflow's executable definition, stored on the run."""
    return {
        "workflow_id": wf.id,
        "name": wf.name,
        "version": wf.version,
        "story_execution": wf.story_execution,
        "steps": normalize_steps(wf.steps),
    }


def step_policy(snapshot: Any, agent: str) -> dict[str, Any] | None:
    """Return ``{"trigger", "enabled"}`` for `agent` from a run's workflow snapshot, or None when
    the snapshot is absent/has no entry for that agent. Used by policy resolution."""
    if not isinstance(snapshot, dict):
        return None
    for s in snapshot.get("steps") or []:
        if isinstance(s, dict) and s.get("agent") == agent:
            return {"trigger": s.get("trigger", "manual"), "enabled": bool(s.get("enabled", True))}
    return None


async def list_workflows(
    session: AsyncSession, *, include_disabled: bool = False
) -> list[Workflow]:
    stmt = select(Workflow).order_by(Workflow.is_default.desc(), Workflow.name)
    if not include_disabled:
        stmt = stmt.where(Workflow.disabled.is_(False))
    return list((await session.execute(stmt)).scalars().all())


async def get_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    return await session.get(Workflow, workflow_id)


async def default_workflow(session: AsyncSession) -> Workflow | None:
    """The single enabled default workflow, or None (engine then uses the built-in flow)."""
    stmt = select(Workflow).where(
        Workflow.is_default.is_(True), Workflow.disabled.is_(False)
    )
    return (await session.execute(stmt)).scalars().first()


async def create_workflow(
    session: AsyncSession,
    *,
    name: str,
    steps: Any,
    story_execution: str = "all",
    description: str = "",
    is_default: bool = False,
) -> Workflow:
    wf = Workflow(
        name=name.strip() or "Untitled workflow",
        description=description.strip(),
        steps=normalize_steps(steps),
        story_execution=story_execution if story_execution in STORY_EXECUTIONS else "all",
        is_default=False,
        disabled=False,
        version=1,
    )
    session.add(wf)
    await session.flush()  # assign id
    if is_default:
        await set_default_workflow(session, wf.id)
    return wf


async def update_workflow(session: AsyncSession, workflow_id: int, **fields: Any) -> Workflow | None:
    """Update a workflow and bump its version. Editing applies to NEW runs only (existing runs read
    their snapshot). `is_default` is handled via `set_default_workflow` to keep the single-default
    invariant."""
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        return None
    if "name" in fields:
        wf.name = str(fields["name"]).strip() or wf.name
    if "description" in fields:
        wf.description = str(fields["description"]).strip()
    if "steps" in fields:
        wf.steps = normalize_steps(fields["steps"])
    if "story_execution" in fields and fields["story_execution"] in STORY_EXECUTIONS:
        wf.story_execution = fields["story_execution"]
    wf.version += 1
    await session.flush()
    if fields.get("is_default"):
        await set_default_workflow(session, wf.id)
    return wf


async def set_default_workflow(session: AsyncSession, workflow_id: int) -> None:
    """Make `workflow_id` the sole default (clears the flag on all others)."""
    for wf in (await session.execute(select(Workflow))).scalars().all():
        wf.is_default = wf.id == workflow_id
    await session.flush()


async def disable_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    """Soft-delete: exclude from the run dropdown but keep readable for historical runs."""
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        return None
    wf.disabled = True
    wf.is_default = False  # a disabled workflow can't be the default
    await session.flush()
    return wf


async def enable_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        return None
    wf.disabled = False
    await session.flush()
    return wf


async def clone_workflow(session: AsyncSession, workflow_id: int) -> Workflow | None:
    src = await session.get(Workflow, workflow_id)
    if src is None:
        return None
    return await create_workflow(
        session,
        name=f"{src.name} (copy)",
        steps=normalize_steps(src.steps),
        story_execution=src.story_execution,
        description=src.description,
        is_default=False,
    )
