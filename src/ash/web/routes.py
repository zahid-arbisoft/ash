"""Server-rendered UI routes (Jinja2). Configuration lives in the SQLAdmin portal at /admin."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ash.config.settings import (
    KNOWN_AGENTS,
    PROJECTS_DIR,
    RUNTIME_DIR,
    get_settings,
    load_project,
)
from ash.db.base import get_session
from ash.db.models import Connector, RunRecord, SpecRecord
from ash.db.runs import list_dev_runs, list_workbench_runs, search_spec_records
from ash.graph.runner import Runner

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

INTAKE_MODES = ["raw_to_spec", "spec_ready", "raw_to_dev"]
# agents wired into the graph (rfc is now real but opt-in via trigger=auto — plan §10.6)
BUILT_AGENTS = {"pm", "research", "dev", "reviewer", "fixer", "rfc"}


def _projects() -> list[str]:
    return sorted(p.stem for p in PROJECTS_DIR.glob("*.yaml"))


def _runner(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    connectors = list((await session.execute(select(Connector))).scalars().all())
    runs = list(
        (await session.execute(select(RunRecord).order_by(RunRecord.created_at.desc()).limit(10)))
        .scalars()
        .all()
    )
    # Analytics KPI (F8): tokens + time burned in the last 7 days.
    try:
        from ash.db.metrics import project_window_totals

        week = await project_window_totals(session, days=7)
    except Exception:  # noqa: BLE001 — analytics are best-effort
        week = {}
    # Live status counts for the dashboard KPIs.
    status_rows = (
        await session.execute(
            select(RunRecord.status, func.count()).group_by(RunRecord.status)
        )
    ).all()
    counts = {str(s or "running"): n for s, n in status_rows}
    active = counts.get("running", 0)
    awaiting = sum(
        n for s, n in counts.items()
        if s in ("awaiting_review", "awaiting_trigger", "awaiting_merge")
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "connectors": connectors,
            "runs": runs,
            "week_metrics": week,
            "active_runs": active,
            "awaiting_runs": awaiting,
            "total_runs": sum(counts.values()),
        },
    )


_PER_PAGE = 20


@router.get("/ui/runs", response_class=HTMLResponse)
async def runs_list(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(default=1, ge=1),
    project: str = Query(default=""),
    mode: str = Query(default=""),
) -> HTMLResponse:
    offset = (page - 1) * _PER_PAGE
    q = select(RunRecord).order_by(RunRecord.created_at.desc())
    if project:
        q = q.where(RunRecord.project == project)
    if mode:
        q = q.where(RunRecord.intake_mode == mode)
    total: int = (
        await session.execute(select(func.count()).select_from(q.subquery()))
    ).scalar_one()
    runs = list((await session.execute(q.limit(_PER_PAGE).offset(offset))).scalars().all())
    pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    return templates.TemplateResponse(
        request,
        "runs_list.html",
        {
            "runs": runs,
            "page": page,
            "pages": pages,
            "total": total,
            "projects": _projects(),
            "modes": INTAKE_MODES,
            "filter_project": project,
            "filter_mode": mode,
        },
    )


# columns for the work board, in flow order (status → label)
_BOARD_COLUMNS = [
    ("running", "In progress"),
    ("awaiting_review", "Awaiting review"),
    ("completed", "Done"),
    ("failed", "Failed"),
]


@router.get("/ui/work", response_class=HTMLResponse)
async def work_board(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    """Jira-style board: recent runs grouped into columns by status."""
    runs = list(
        (
            await session.execute(
                select(RunRecord).order_by(RunRecord.created_at.desc()).limit(200)
            )
        )
        .scalars()
        .all()
    )
    columns = [
        {"key": key, "label": label, "runs": [r for r in runs if (r.status or "running") == key]}
        for key, label in _BOARD_COLUMNS
    ]
    return templates.TemplateResponse(request, "work_board.html", {"columns": columns})


# ── Workflows (workflow-builder) ───────────────────────────────────────────────


def _steps_from_form(form: Any) -> list[dict[str, Any]]:
    """Build a step list from the builder form. Per-agent fields (`enabled_<agent>` checkbox +
    `trigger_<agent>` select) are authoritative — robust even if JS is off. An optional `order`
    (comma-separated agent names) sets the authored order for forward-compat; `normalize_steps`
    re-canonicalises for execution (v1 runs in pipeline order, OD1)."""
    from ash.db.workflows import WORKFLOW_AGENTS

    order_raw = str(form.get("order") or "")
    ordered = [a for a in (s.strip() for s in order_raw.split(",")) if a in WORKFLOW_AGENTS]
    agents = ordered or list(WORKFLOW_AGENTS)
    for a in WORKFLOW_AGENTS:  # ensure every agent is present
        if a not in agents:
            agents.append(a)
    return [
        {
            "agent": a,
            "enabled": form.get(f"enabled_{a}") is not None,
            "trigger": "auto" if form.get(f"trigger_{a}") == "auto" else "manual",
        }
        for a in agents
    ]


@router.get("/ui/workflows", response_class=HTMLResponse)
async def workflows_list(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    from ash.db.workflows import WORKFLOW_AGENTS, STORY_EXECUTIONS, list_workflows

    workflows = await list_workflows(session, include_disabled=True)
    return templates.TemplateResponse(
        request,
        "workflows.html",
        {
            "workflows": workflows,
            "agents": list(WORKFLOW_AGENTS),
            "story_executions": list(STORY_EXECUTIONS),
        },
    )


@router.post("/ui/workflows")
async def workflow_create(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> RedirectResponse:
    from ash.db.workflows import create_workflow

    form = await request.form()
    await create_workflow(
        session,
        name=str(form.get("name") or "Untitled workflow"),
        description=str(form.get("description") or ""),
        steps=_steps_from_form(form),
        story_execution=str(form.get("story_execution") or "all"),
        is_default=form.get("is_default") is not None,
    )
    await session.commit()
    return RedirectResponse(url="/ui/workflows", status_code=303)


@router.post("/ui/workflows/{wid}/update")
async def workflow_update(
    request: Request, wid: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RedirectResponse:
    from ash.db.workflows import update_workflow

    form = await request.form()
    await update_workflow(
        session,
        wid,
        name=str(form.get("name") or ""),
        description=str(form.get("description") or ""),
        steps=_steps_from_form(form),
        story_execution=str(form.get("story_execution") or "all"),
        is_default=form.get("is_default") is not None,
    )
    await session.commit()
    return RedirectResponse(url="/ui/workflows", status_code=303)


@router.post("/ui/workflows/{wid}/default")
async def workflow_set_default(
    wid: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RedirectResponse:
    from ash.db.workflows import set_default_workflow

    await set_default_workflow(session, wid)
    await session.commit()
    return RedirectResponse(url="/ui/workflows", status_code=303)


@router.post("/ui/workflows/{wid}/disable")
async def workflow_disable(
    wid: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RedirectResponse:
    from ash.db.workflows import disable_workflow

    await disable_workflow(session, wid)
    await session.commit()
    return RedirectResponse(url="/ui/workflows", status_code=303)


@router.post("/ui/workflows/{wid}/enable")
async def workflow_enable(
    wid: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RedirectResponse:
    from ash.db.workflows import enable_workflow

    await enable_workflow(session, wid)
    await session.commit()
    return RedirectResponse(url="/ui/workflows", status_code=303)


@router.post("/ui/workflows/{wid}/clone")
async def workflow_clone(
    wid: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RedirectResponse:
    from ash.db.workflows import clone_workflow

    await clone_workflow(session, wid)
    await session.commit()
    return RedirectResponse(url="/ui/workflows", status_code=303)


@router.get("/ui/connectors", response_class=HTMLResponse)
async def connectors_list(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    from ash.integrations.mcp import is_mcp
    from ash.integrations.service import validate_connector

    connectors = list(
        (await session.execute(select(Connector).order_by(Connector.name))).scalars().all()
    )
    rows = [{"c": c, "issues": validate_connector(c), "mcp": is_mcp(c)} for c in connectors]
    return templates.TemplateResponse(request, "connectors.html", {"rows": rows})


@router.get("/ui/runs/new", response_class=HTMLResponse)
async def run_new(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    sources = list(
        (await session.execute(select(Connector).where(Connector.is_source, Connector.enabled)))
        .scalars()
        .all()
    )
    sinks = list(
        (await session.execute(select(Connector).where(Connector.is_sink, Connector.enabled)))
        .scalars()
        .all()
    )
    from ash.db.workflows import default_workflow, list_workflows

    workflows = await list_workflows(session)
    default_wf = await default_workflow(session)
    return templates.TemplateResponse(
        request,
        "run_new.html",
        {
            "sources": sources,
            "sinks": sinks,
            "projects": _projects(),
            "modes": INTAKE_MODES,
            "workflows": workflows,
            "default_workflow_id": default_wf.id if default_wf else None,
        },
    )


@router.post("/ui/runs")
async def run_create(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    project: Annotated[str, Form()],
    item_id: Annotated[str, Form()] = "upload",
    intake_mode: Annotated[str, Form()] = "raw_to_spec",
    integration_id: Annotated[str, Form()] = "",
    task_sink_id: Annotated[str, Form()] = "",
    ticket_id: Annotated[str, Form()] = "",
    story_mode: Annotated[str, Form()] = "single",
    pr_strategy: Annotated[str, Form()] = "per_story",
    workflow_id: Annotated[str, Form()] = "",
    pm_only: Annotated[str, Form()] = "",
    run_prompt: Annotated[str, Form()] = "",
    attachments: list[UploadFile] = File(default=[]),  # noqa: B008 — FastAPI form dependency
) -> RedirectResponse:
    int_id = int(integration_id) if integration_id else None
    sink_id = int(task_sink_id) if task_sink_id else None
    mode = story_mode if story_mode in ("single", "multiple") else "single"
    # PR packaging (F7): combined PR only makes sense for multi-story runs.
    pr_strat = "single" if (pr_strategy == "single" and mode == "multiple") else "per_story"
    # Workflow selection (workflow-builder): snapshot the chosen workflow (or the default) so this
    # run resolves agent triggers against a frozen definition; empty = built-in flow.
    wf_id = int(workflow_id) if workflow_id.strip().isdigit() else None
    wf_snapshot: dict[str, Any] = {}
    if wf_id is not None:
        from ash.db.workflows import get_workflow, snapshot_for

        wf = await get_workflow(session, wf_id)
        if wf is not None and not wf.disabled:
            wf_snapshot = snapshot_for(wf)
        else:
            wf_id = None
    # Decision #33: every run is cockpit-driven (manual gates throughout); pm_only stays as an
    # opt-in flag (kept for back-compat / API) but the form no longer sets it — the client simply
    # stops triggering build agents if they only want a spec.
    workbench = pm_only.strip().lower() in ("1", "true", "on", "yes")
    paths = await _save_uploads(attachments)
    run_id = await _runner(request).start_run(
        project=project,
        item_id=item_id or "upload",
        intake_mode=intake_mode,
        integration_id=int_id,
        attachments=paths,
        task_sink_id=sink_id,
        ticket_id=ticket_id.strip(),
        story_mode=mode,
        pr_strategy=pr_strat,
        pm_only=workbench,
        run_prompt=run_prompt.strip(),
        workflow_snapshot=wf_snapshot,
    )
    session.add(
        RunRecord(
            run_id=run_id,
            project=project,
            integration_id=int_id,
            task_sink_id=sink_id,
            item_id=item_id or "upload",
            intake_mode=intake_mode,
            ticket_id=ticket_id.strip(),
            story_mode=mode,
            pr_strategy=pr_strat,
            pm_only=workbench,
            workflow_id=wf_id,
            workflow_snapshot=wf_snapshot or None,
        )
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/run/{run_id}", status_code=303)


async def _save_uploads(files: list[UploadFile]) -> list[str]:
    real = [f for f in files if f.filename]
    if not real:
        return []
    dest = RUNTIME_DIR / "uploads" / uuid.uuid4().hex
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for f in real:
        target = dest / (f.filename or "file").replace("/", "_")
        target.write_bytes(await f.read())
        paths.append(str(target))
    return paths


@router.get("/ui/runs/{run_id}/attachment/{index}")
async def run_attachment(request: Request, run_id: str, index: int) -> Any:
    """Serve an uploaded attachment file inline (text/plain or original mime type)."""
    from fastapi.responses import FileResponse, PlainTextResponse

    state = await _runner(request).get_run(run_id)
    if state is None:
        return PlainTextResponse("run not found", status_code=404)
    attachments: list[str] = state.get("attachments") or []
    if index < 0 or index >= len(attachments):
        return PlainTextResponse("attachment not found", status_code=404)
    path = Path(attachments[index])
    exists = await asyncio.to_thread(path.exists)
    if not exists:
        return PlainTextResponse("file not found on disk", status_code=404)
    suffix = path.suffix.lower()
    text_suffixes = {".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml"}
    if suffix in text_suffixes:
        text = await asyncio.to_thread(path.read_text, errors="replace")
        return PlainTextResponse(text, media_type="text/plain")
    return FileResponse(path)


async def _task_statuses(run_id: str) -> dict[str, str]:
    """Return {agent_name: status} for all AgentTask rows belonging to this run."""
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import list_tasks_for_run

        async with get_sessionmaker()() as session:
            tasks = await list_tasks_for_run(session, run_id)
            return {t.agent_name: t.status for t in tasks}
    except Exception:  # noqa: BLE001
        return {}


async def _run_metrics(run_id: str) -> dict[str, Any]:
    """Analytics for the run timeline (F8): {totals, by_story[ticket][agent]}. Best-effort."""
    out: dict[str, Any] = {"totals": {}, "by_story": {}}
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.metrics import run_breakdown, run_totals

        async with get_sessionmaker()() as session:
            out["totals"] = await run_totals(session, run_id)
            breakdown = await run_breakdown(session, run_id)
        by_story: dict[str, dict[str, Any]] = {}
        for (ticket, agent), vals in breakdown.items():
            by_story.setdefault(ticket, {})[agent] = vals
        out["by_story"] = by_story
    except Exception:  # noqa: BLE001 — analytics are best-effort
        pass
    return out


async def _inprogress_tasks(run_id: str) -> list[tuple[str, str]]:
    """`(agent_name, ticket_id)` for every AgentTask of this run currently `in_progress`, most
    recently started first. Used to locate which stage is running (or stalled). Best-effort."""
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import list_tasks_for_run

        async with get_sessionmaker()() as session:
            tasks = await list_tasks_for_run(session, run_id)
    except Exception:  # noqa: BLE001 — best-effort
        return []
    active = [t for t in tasks if t.status == "in_progress"]
    active.sort(key=lambda t: (t.started_at or t.created_at), reverse=True)
    return [(t.agent_name, t.ticket_id or "") for t in active]


def _has_pending_gate(state: dict[str, Any]) -> bool:
    """True when the run is paused at a HITL gate (trigger/review/merge) awaiting the human."""
    return bool(
        state.get("pending_trigger")
        or state.get("pending_review")
        or state.get("pending_merge")
    )


_RUN_NODE_TO_STAGE = {"pm": "pm", "pm_publish": "pm", "rfc": "rfc", "intake": "intake"}


def _running_build_stage(state: dict[str, Any], story: dict[str, Any]) -> str | None:
    """Within the current story, the build step actually executing = the first one (in pipeline
    order) that isn't done/skipped/failed. Derived from story state, NOT the best-effort AgentTask
    table — so it stays correct after a retrigger/retry that leaves stale `in_progress` rows."""
    for stage in _BUILD_STAGES:
        st = _story_stage_status(state, story, stage)
        if st == "awaiting":
            return None  # gated, not running
        if st == "pending":
            return stage  # first incomplete step → the one running/about to run
        # done / skipped / failed → look at the next step
    return None


def _live_stage(state: dict[str, Any]) -> tuple[str | None, str]:
    """The stage the run is currently working on, as `(stage, story)`. Build phase → the current
    story's first incomplete step; run-level (pm/rfc) → the graph's next node. Authoritative
    (graph + story state), independent of the AgentTask table."""
    cur = state.get("current_story") or ""
    stories = state.get("stories") or {}
    if cur and cur in stories:
        stage = _running_build_stage(state, stories[cur])
        return (stage, cur) if stage else (None, "")
    node = next(iter(state.get("_next_nodes") or []), "")
    return (_RUN_NODE_TO_STAGE.get(node), "")


async def _augment_liveness(run_id: str, state: dict[str, Any]) -> None:
    """Annotate `state` with `running_stage`/`running_story` (a stage actively executing) or
    `stalled`/`stalled_stage`/`stalled_story` (the run is marked running but no live task is driving
    it — e.g. the server restarted mid-run). Decision #33 follow-up.

    The live stage is derived from the graph + story state (authoritative) rather than the
    best-effort AgentTask table, so a retrigger/retry that leaves a stale `in_progress` row never
    makes the cockpit show the wrong agent. The task table is only a stalled fallback."""
    if state.get("status") != "running":
        return
    stage, story = _live_stage(state)
    if state.get("_task_running"):
        if stage:
            state["running_stage"] = stage
            state["running_story"] = story
        return
    # Not driven by a live task and not paused at a gate → orphaned (stalled).
    if _has_pending_gate(state):
        return
    if stage:
        state["stalled"] = True
        state["stalled_stage"] = stage
        state["stalled_story"] = story
        return
    # Couldn't derive a stage from graph/story state — fall back to the task table.
    tasks = await _inprogress_tasks(run_id)
    if tasks:
        agent, ticket = tasks[0]
        state["stalled"] = True
        state["stalled_stage"] = "dev" if agent == "coding" else agent
        state["stalled_story"] = ticket


async def _agent_triggers(project: str) -> dict[str, str]:
    """Resolved trigger mode (DB > YAML > default) per agent, for the run page's manual-trigger
    controls. Best-effort: empty dict if config/DB unavailable."""
    out: dict[str, str] = {}
    if not project:
        return out
    try:
        from ash.config.settings import KNOWN_AGENTS, load_project

        proj = load_project(project)
    except Exception:  # noqa: BLE001
        return out
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import resolve_policy

        async with get_sessionmaker()() as session:
            for name in KNOWN_AGENTS:
                out[name] = (await resolve_policy(session, proj, name)).trigger
    except Exception:  # noqa: BLE001 — no DB → fall back to YAML/defaults
        for name in KNOWN_AGENTS:
            out[name] = proj.agent_policy(name).trigger
    return out


async def _agent_enabled(project: str) -> dict[str, bool]:
    """Resolved `enabled` flag (DB > YAML > default) per agent, so the cockpit can hide controls
    for disabled agents (e.g. drop the 'Approve & write RFC' button when RFC is off). Best-effort:
    empty dict (callers default to enabled) if config/DB unavailable."""
    out: dict[str, bool] = {}
    if not project:
        return out
    try:
        from ash.config.settings import KNOWN_AGENTS, load_project

        proj = load_project(project)
    except Exception:  # noqa: BLE001
        return out
    try:
        from ash.db.base import get_sessionmaker
        from ash.db.tasks import resolve_policy

        async with get_sessionmaker()() as session:
            for name in KNOWN_AGENTS:
                out[name] = (await resolve_policy(session, proj, name)).enabled
    except Exception:  # noqa: BLE001 — no DB → fall back to YAML/defaults
        for name in KNOWN_AGENTS:
            out[name] = proj.agent_policy(name).enabled
    return out


@router.get("/ui/runs/{run_id}")
async def run_status(run_id: str) -> RedirectResponse:
    """Legacy run-timeline path → redirected to the run cockpit (decision #33)."""
    return RedirectResponse(url=f"/ui/run/{run_id}", status_code=308)


@router.get("/ui/runs/{run_id}/llm")
async def run_llm_io(run_id: str) -> RedirectResponse:
    """Legacy per-run LLM I/O path → redirected to the cockpit I/O view (decision #33)."""
    return RedirectResponse(url=f"/ui/run/{run_id}/io", status_code=308)


# ── U1: live run status over SSE + the HITL approval gate ────────────────────


def _sse(data: str, *, event: str | None = None) -> str:
    """Format an HTML fragment as a Server-Sent Event (each line gets a `data:` prefix)."""
    lines = [f"event: {event}"] if event else []
    lines += [f"data: {line}" for line in data.split("\n")]
    return "\n".join(lines) + "\n\n"


_TERMINAL = {"completed", "failed", "cancelled"}


@router.get("/ui/runs/{run_id}/events")
async def run_events(request: Request, run_id: str) -> StreamingResponse:
    """Stream the run timeline partial as it changes; HTMX swaps it in (no fixed-interval poll)."""
    runner = _runner(request)
    timeline = templates.get_template("_run_timeline.html")

    async def gen() -> AsyncIterator[str]:
        for _ in range(800):  # safety bound (~20 min at 1.5s); client reconnects if needed
            if await request.is_disconnected():
                return
            state = await runner.get_run(run_id)
            ts = await _task_statuses(run_id)
            metrics = await _run_metrics(run_id)
            triggers = await _agent_triggers((state or {}).get("project", ""))
            yield _sse(
                timeline.render(
                    run_id=run_id,
                    state=state or {},
                    task_statuses=ts,
                    metrics=metrics,
                    triggers=triggers,
                ),
                event="message",
            )
            if (state or {}).get("status") in _TERMINAL:
                yield _sse("", event="done")
                return
            await asyncio.sleep(1.5)
        yield _sse("", event="done")

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _render_timeline(request: Request, run_id: str) -> HTMLResponse:
    """Render the run-timeline partial for the current run state (HTMX innerHTML swap)."""
    state = await _runner(request).get_run(run_id)
    ts = await _task_statuses(run_id)
    metrics = await _run_metrics(run_id)
    triggers = await _agent_triggers((state or {}).get("project", ""))
    return templates.TemplateResponse(
        request,
        "_run_timeline.html",
        {
            "run_id": run_id,
            "state": state or {},
            "task_statuses": ts,
            "metrics": metrics,
            "triggers": triggers,
        },
    )


async def _decide(
    request: Request, run_id: str, decision: Any, *, background: bool = False
) -> HTMLResponse:
    await _runner(request).resume_run(run_id, decision, background=background)
    return await _render_timeline(request, run_id)


@router.post("/ui/runs/{run_id}/approve", response_class=HTMLResponse)
async def run_approve(
    request: Request, run_id: str, stories: list[str] = Form(default=[])  # noqa: B008
) -> HTMLResponse:
    """Approve a spec-review or merge gate. When `stories` are submitted (the spec gate's
    per-story checkboxes), pass them so only those tickets become stories (§4.2)."""
    from ash.observability import langsmith as _ls
    _ls.score(run_id, "hitl_decision", 1.0, comment="Approved by user")
    chosen = [s for s in stories if s.strip()]
    decision: Any = {"action": "approve", "stories": chosen} if chosen else "approve"
    return await _decide(request, run_id, decision)


@router.post("/ui/runs/{run_id}/reject", response_class=HTMLResponse)
async def run_reject(request: Request, run_id: str) -> HTMLResponse:
    from ash.observability import langsmith as _ls
    _ls.score(run_id, "hitl_decision", -1.0, comment="Rejected by user")
    return await _decide(request, run_id, "reject")


@router.post("/ui/runs/{run_id}/trigger", response_class=HTMLResponse)
async def run_trigger_agent(request: Request, run_id: str) -> HTMLResponse:
    """Resume a run paused at a manual-trigger gate (A4). decision='run' activates the agent.

    Runs in the background so the (potentially long) agent doesn't block the request and can be
    stopped mid-run; the timeline streams progress over SSE.

    The trigger/skip buttons are cleared immediately in the response (before the background task
    updates the checkpoint) so they don't linger; SSE delivers the real in-progress state within
    ~1.5 s."""
    runner = _runner(request)
    state = dict(await runner.get_run(run_id) or {})
    triggered_agent: str = state.get("pending_trigger") or ""
    triggered_story: str = state.get("pending_story") or ""

    # Start the agent in the background — does not wait for it to finish.
    await runner.resume_run(run_id, "run", background=True)

    # Override state so the Trigger/Skip UI disappears instantly; SSE corrects within 1-2 ticks.
    state["pending_trigger"] = None
    state["pending_story"] = None
    state["status"] = "running"
    # Flip the relevant story to "running" so the story card reflects the change.
    stories = state.get("stories") or {}
    if triggered_story and triggered_story in stories:
        story = stories[triggered_story]
        if hasattr(story, "model_copy"):
            stories[triggered_story] = story.model_copy(update={"status": "running"})
        elif isinstance(story, dict):
            stories[triggered_story] = {**story, "status": "running"}

    ts = await _task_statuses(run_id)
    # Force the triggered agent to in_progress so its stage row shows running immediately.
    if triggered_agent:
        ts[triggered_agent] = "in_progress"

    metrics = await _run_metrics(run_id)
    triggers = await _agent_triggers(state.get("project", ""))
    return templates.TemplateResponse(
        request,
        "_run_timeline.html",
        {
            "run_id": run_id,
            "state": state,
            "task_statuses": ts,
            "metrics": metrics,
            "triggers": triggers,
        },
    )


@router.post("/ui/runs/{run_id}/skip", response_class=HTMLResponse)
async def run_skip_agent(request: Request, run_id: str) -> HTMLResponse:
    """Skip a manual-trigger agent and continue the pipeline (decision != 'run' → the gate
    records a skip note and the graph advances). Lets the human pass on e.g. RFC."""
    return await _decide(request, run_id, "skip")


@router.post("/ui/runs/{run_id}/stop", response_class=HTMLResponse)
async def run_stop(request: Request, run_id: str) -> HTMLResponse:
    """Stop a running run: cancel the in-flight agent and mark the run cancelled. The checkpoint
    is preserved, so it can be resumed or a story re-run from the per-story controls."""
    await _runner(request).stop_run(run_id)
    return await _render_timeline(request, run_id)


@router.post("/ui/runs/{run_id}/resume-run")
async def run_resume_stopped(request: Request, run_id: str) -> RedirectResponse:
    """Resume a stopped (cancelled) run from its last checkpoint (background); redirect so a fresh
    SSE stream shows live progress."""
    await _runner(request).resume_stopped(run_id)
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.post("/ui/runs/{run_id}/retry")
async def run_retry(
    request: Request, run_id: str, from_step: str = Form(default="")
) -> RedirectResponse:
    """Re-run a failed run from the earliest failure (run-level step or failed story).

    Kicks off the re-run in the background and redirects to the run page so a fresh SSE
    connection streams live progress (the old stream closed when the run first failed).
    """
    await _runner(request).retry_run(run_id, from_step=from_step or None)
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


_STORY_STEPS = ("research", "dev", "reviewer", "fixer")


@router.post("/ui/runs/{run_id}/stories/{ticket_id}/rerun")
async def story_rerun(
    request: Request,
    run_id: str,
    ticket_id: str,
    step: str = Form(default="research"),
) -> RedirectResponse:
    """Retry a failed story, or manually regenerate one of its steps (F3/F4).

    `step` ∈ research|coding|reviewer|fixer — the story is reset from that step onward (preserving
    its branch/PR so the PR is updated, never duplicated) and the story loop resumes at it.
    """
    target = step if step in _STORY_STEPS else "research"
    await _runner(request).retry_run(run_id, ticket_id=ticket_id, from_step=target)
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


async def _connector_health_json(
    connector_id: int, session: AsyncSession
) -> dict[str, Any]:
    """Shared health-check logic used by the JSON and HTMX endpoints."""
    connector = await session.get(Connector, connector_id)
    if connector is None:
        return {"status": "error", "message": "connector not found", "tool_count": 0}

    from ash.integrations.mcp import is_mcp

    if is_mcp(connector):
        try:
            from ash.integrations.service import mcp_tools_for

            tools = await mcp_tools_for(connector_id)
            return {"status": "ok", "transport": "mcp_http", "tool_count": len(tools)}
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "transport": "mcp_http",
                "message": str(exc),
                "tool_count": 0,
            }
    else:
        from ash.integrations.service import validate_connector

        issues = validate_connector(connector)
        if issues:
            return {
                "status": "warn",
                "transport": "builtin",
                "issues": issues,
                "tool_count": 0,
            }
        return {"status": "ok", "transport": "builtin", "tool_count": 0}


@router.get("/ui/connectors/{connector_id}/health")
async def connector_health(
    connector_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Health-check a connector: try loading its tools (MCP) or validate config (built-in)."""
    return await _connector_health_json(connector_id, session)


@router.get("/ui/connectors/{connector_id}/health-dot", response_class=HTMLResponse)
async def connector_health_dot(
    request: Request,
    connector_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """HTMX fragment — a single coloured dot representing the connector's health."""
    result = await _connector_health_json(connector_id, session)
    status = result.get("status", "error")
    colour = {"ok": "bg-ok", "warn": "bg-warn", "error": "bg-danger"}.get(status, "bg-border")
    label = result.get("message") or (
        f"{result.get('tool_count', 0)} tools" if status == "ok" else status
    )
    dot = (
        f'<span class="w-2 h-2 inline-block rounded-full {colour}" '
        f'title="{label}"></span>'
    )
    return HTMLResponse(dot)


@router.post("/ui/connectors/preview-health")
async def connector_preview_health(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    kind: str = Form(default=""),
    transport: str = Form(default=""),
    base_url: str = Form(default=""),
    secret: str = Form(default=""),
    config_json: str = Form(default="{}"),
) -> dict[str, Any]:
    """Wizard step-3: test the connection before saving (no DB write)."""
    from ash.db.models import ConnectorKind
    from ash.integrations.mcp import is_mcp
    from ash.integrations.service import validate_connector

    try:
        kind_enum = ConnectorKind(kind)
        cfg = json.loads(config_json or "{}")
    except (ValueError, json.JSONDecodeError) as exc:
        return {"status": "error", "message": f"bad input: {exc}", "tool_count": 0}

    tmp = Connector(
        name="_preview",
        kind=kind_enum,
        transport=transport.strip() or None,
        base_url=base_url.strip() or None,
        config=cfg,
        secret=secret,
    )
    if is_mcp(tmp):
        if not tmp.base_url:
            return {
                "status": "error",
                "message": "base_url required for MCP transport",
                "tool_count": 0,
            }
        try:
            from ash.integrations.mcp import mcp_tools_for_url

            tools = await mcp_tools_for_url(tmp.base_url, secret)
            return {"status": "ok", "transport": "mcp_http", "tool_count": len(tools)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": str(exc), "tool_count": 0}
    else:
        issues = validate_connector(tmp)
        if issues:
            return {"status": "warn", "issues": issues, "tool_count": 0}
        return {"status": "ok", "transport": "builtin", "tool_count": 0}


@router.post("/ui/connectors", response_class=HTMLResponse)
async def connector_create(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    name: str = Form(...),
    kind: str = Form(...),
    transport: str = Form(default=""),
    base_url: str = Form(default=""),
    secret: str = Form(default=""),
    config_json: str = Form(default="{}"),
    is_source: bool = Form(default=False),
    is_sink: bool = Form(default=False),
    is_default_sink: bool = Form(default=False),
    enabled: bool = Form(default=True),
) -> HTMLResponse:  # also returns RedirectResponse on success
    """Create a connector from the wizard form. Redirects to connectors list on success."""
    from ash.db.models import ConnectorKind

    try:
        kind_enum = ConnectorKind(kind)
        cfg = json.loads(config_json or "{}")
    except (ValueError, json.JSONDecodeError) as exc:
        rows_q = await session.execute(select(Connector).order_by(Connector.name))
        from ash.integrations.mcp import is_mcp
        from ash.integrations.service import validate_connector

        rows = [
            {"c": c, "issues": validate_connector(c), "mcp": is_mcp(c)}
            for c in rows_q.scalars().all()
        ]
        return templates.TemplateResponse(
            request,
            "connectors.html",
            {"rows": rows, "wizard_error": str(exc)},
        )

    connector = Connector(
        name=name.strip(),
        kind=kind_enum,
        transport=transport.strip() or None,
        base_url=base_url.strip() or None,
        config=cfg,
        secret=secret,
        is_source=is_source,
        is_sink=is_sink,
        is_default_sink=is_default_sink,
        enabled=enabled,
    )
    session.add(connector)
    await session.commit()
    return RedirectResponse("/ui/connectors", status_code=303)  # type: ignore[return-value]


_INTERRUPT_LABELS: dict[str, str] = {
    "awaiting_review": "Spec review",
    "awaiting_trigger": "Agent trigger",
    "awaiting_merge": "Merge approval",
}


@router.get("/ui/approvals", response_class=HTMLResponse)
async def approvals(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    """Queue of runs paused at any HITL gate (spec review / agent trigger / merge approval)."""
    rows = list(
        (
            await session.execute(
                select(RunRecord)
                .where(RunRecord.status.in_(list(_INTERRUPT_LABELS.keys())))
                .order_by(RunRecord.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    # Enrich each row with a human-readable label for the type of gate it's paused at.
    enriched = [
        {"record": r, "gate_label": _INTERRUPT_LABELS.get(r.status, r.status)}
        for r in rows
    ]
    return templates.TemplateResponse(request, "approvals.html", {"runs": enriched})


# ── U2: searchable PM runs ───────────────────────────────────────────────────


@router.get("/ui/pm-runs", response_class=HTMLResponse)
async def pm_runs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(default=1, ge=1),
    q: str = Query(default=""),
    project: str = Query(default=""),
) -> HTMLResponse:
    rows, total = await search_spec_records(
        session, query=q, project=project, page=page, per_page=_PER_PAGE
    )
    pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    ctx = {
        "rows": rows,
        "page": page,
        "pages": pages,
        "total": total,
        "q": q,
        "filter_project": project,
        "projects": _projects(),
    }
    # HTMX search-as-you-type swaps just the results table
    template = "_pm_runs_results.html" if request.headers.get("HX-Request") else "pm_runs.html"
    return templates.TemplateResponse(request, template, ctx)


@router.get("/ui/pm-workbench")
async def pm_workbench_list() -> RedirectResponse:
    """Legacy PM workbench list → the run hub list (decision #33)."""
    return RedirectResponse(url="/ui/runs", status_code=308)


@router.get("/ui/pm-runs/{run_id}", response_class=HTMLResponse)
async def pm_run_detail(
    request: Request, run_id: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    rec = await session.get(SpecRecord, run_id)
    return templates.TemplateResponse(request, "pm_run_detail.html", {"rec": rec, "run_id": run_id})


# ── PM workbench: run PM standalone, review & iterate on stories (decision #29) ──────────────
# NOTE: /ui/pm/new MUST be declared before /ui/pm/{run_id} so it isn't captured as a run_id.


@router.get("/ui/pm/new")
async def pm_new() -> RedirectResponse:
    """Legacy PM-run form → the unified new-run form (decision #33)."""
    return RedirectResponse(url="/ui/runs/new", status_code=308)


@router.get("/ui/pm/{run_id}")
async def pm_workbench(run_id: str) -> RedirectResponse:
    """Legacy PM workbench → the PM stage of the run cockpit (decision #33)."""
    return RedirectResponse(url=f"/ui/run/{run_id}/pm", status_code=308)


@router.get("/ui/dev-workbench")
async def dev_workbench_list() -> RedirectResponse:
    """Legacy Dev workbench list → the run hub list (decision #33)."""
    return RedirectResponse(url="/ui/runs", status_code=308)


@router.get("/ui/dev/{run_id}")
async def dev_workbench(run_id: str) -> RedirectResponse:
    """Legacy Dev workbench → the Dev stage of the run cockpit (decision #33)."""
    return RedirectResponse(url=f"/ui/run/{run_id}/dev", status_code=308)


@router.get("/ui/dev/{run_id}/llm")
async def dev_llm_io(run_id: str) -> RedirectResponse:
    """Legacy Dev LLM I/O → the cockpit I/O view (decision #33)."""
    return RedirectResponse(url=f"/ui/run/{run_id}/io", status_code=308)



async def _io_view(
    request: Request,
    session: AsyncSession,
    *,
    run_id: str | None = None,
    agent: str = "",
    ticket: str = "",
    phase: str = "",
    query: str = "",
    back_to: str | None = None,
) -> HTMLResponse:
    """Shared LLM-I/O view (decision #33 / Phase D): lists exchanges, optionally filtered by run /
    agent / story / phase / free-text, grouped by (story, agent) in capture order, with token
    analytics. Renders the dedicated `io_log.html`."""
    from ash.db.exchanges import list_exchanges

    rows = await list_exchanges(
        session, run_id=run_id, agent=agent or None, ticket=ticket or None,
        phase=phase or None, query=query or None,
    )
    # Per-(story, agent) wall-clock from the metrics table, so each group can show time consumed
    # alongside tokens. Only meaningful when scoped to one run (global spans many runs). (F6)
    by_story_ms: dict[tuple[str, str], int] = {}
    if run_id is not None:
        metrics = await _run_metrics(run_id)
        for tk, agents in (metrics.get("by_story") or {}).items():
            for ag, vals in (agents or {}).items():
                by_story_ms[(tk or "", ag)] = int((vals or {}).get("duration_ms", 0) or 0)
    groups: list[dict[str, Any]] = []
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    tot_prompt = tot_completion = tot_duration = 0
    for r in rows:
        tot_prompt += r.prompt_tokens or 0
        tot_completion += r.completion_tokens or 0
        key = (r.run_id, r.ticket_id or "", r.agent_name)
        grp = index.get(key)
        if grp is None:
            canonical = _AGENT_ALIAS.get(r.agent_name, r.agent_name)
            grp = {
                "run_id": r.run_id,
                "ticket_id": r.ticket_id or "",
                "agent_name": r.agent_name,
                "agent_label": _STAGE_LABELS.get(canonical, r.agent_name),
                "exchanges": [],
                "prompt": 0,
                "completion": 0,
                # metrics record under the canonical name (dev), exchanges may be historical (coding)
                "duration_ms": by_story_ms.get((r.ticket_id or "", canonical), 0),
            }
            index[key] = grp
            groups.append(grp)
            tot_duration += grp["duration_ms"]
        grp["exchanges"].append(r)
        grp["prompt"] += r.prompt_tokens or 0
        grp["completion"] += r.completion_tokens or 0
    # In-flight: agents currently running for this run whose response hasn't landed yet — surfaces
    # long-running calls (e.g. Dev) that have no persisted exchange yet (F6). Scoped runs only.
    in_flight: list[dict[str, str]] = []
    if run_id is not None:
        for ag, tk in await _inprogress_tasks(run_id):
            canonical = _AGENT_ALIAS.get(ag, ag)
            in_flight.append({"label": _STAGE_LABELS.get(canonical, ag), "ticket": tk})
    ctx = {
        "run_id": run_id,
        "groups": groups,
        "total": len(rows),
        "tot_prompt": tot_prompt,
        "tot_completion": tot_completion,
        "tot_duration": tot_duration,
        "in_flight": in_flight,
        "phase_glossary": _PHASE_GLOSSARY,
        "f_agent": agent,
        "f_ticket": ticket,
        "f_phase": phase,
        "f_query": query,
        "back_to": back_to,
        "agent_options": [a for a in KNOWN_AGENTS],
        "scoped": run_id is not None,
    }
    template = "_io_log_results.html" if request.headers.get("HX-Request") else "io_log.html"
    return templates.TemplateResponse(request, template, ctx)


# Historical agent-name alias for display (coding→dev rename, decision #33).
_AGENT_ALIAS = {"coding": "dev"}

# What each LLM-exchange phase means, shown as a legend on the I/O page (F6). The phase tells the
# client *why* a given call was made; tool-using agents do explore→extract, tool-free agents
# (PM/RFC) do a single_call, and workbench edits are refine.
_PHASE_GLOSSARY: list[dict[str, str]] = [
    {"phase": "single_call",
     "desc": "One direct structured call (no tools). Tool-free agents (PM, RFC) use this."},
    {"phase": "explore",
     "desc": "A step in the tool loop: the agent reads files / greps the repo. Numbered "
             "(e.g. 'explore 3') up to the EXPLORE_STEPS ceiling — the model stops early once it "
             "has enough, so the count varies per run."},
    {"phase": "extract",
     "desc": "The tool-free structured pass that turns the exploration notes into the typed "
             "result (plan / change / review)."},
    {"phase": "refine",
     "desc": "A workbench re-run applying human feedback to one ticket/agent in place."},
]


# ── Run cockpit (decision #33) ────────────────────────────────────────────────
# One page per run: a pipeline rail (Intake→PM→RFC→Research→Dev→Reviewer→Fixer) with live
# status + token chips; clicking a stage opens that agent's workbench panel below. Build stages
# (research/dev/reviewer/fixer) are per-story; the rail reflects the selected story.

_RUN_STAGES: tuple[str, ...] = ("intake", "pm", "rfc")
_BUILD_STAGES: tuple[str, ...] = ("research", "dev", "reviewer", "fixer")
_ALL_STAGES: tuple[str, ...] = _RUN_STAGES + _BUILD_STAGES
_STAGE_LABELS = {
    "intake": "Intake", "pm": "PM", "rfc": "RFC",
    "research": "Research", "dev": "Dev", "reviewer": "Reviewer", "fixer": "Fixer",
}
_STAGE_ICONS = {
    "intake": "⛓", "pm": "✦", "rfc": "📄",
    "research": "🔍", "dev": "⌨", "reviewer": "✓", "fixer": "🔧",
}
_STAGE_OUTPUT_KEYS = {
    "research": ("plan", "doc_ref"),
    "dev": ("change", "pr_url", "files_written"),
    "reviewer": ("review", "verdict"),
    "fixer": ("change", "files_written"),
}


def _story_ids(state: dict[str, Any]) -> list[str]:
    return list(state.get("story_order") or list((state.get("stories") or {}).keys()))


def _ns_status(ns: dict[str, Any], output_keys: tuple[str, ...]) -> str:
    """Derive a stage status from a sub-state namespace dict."""
    if not isinstance(ns, dict) or not ns:
        return "pending"
    if ns.get("error"):
        return "failed"
    note = ns.get("note") or ""
    if note.startswith("skipped"):
        return "skipped"
    if any(ns.get(k) for k in output_keys) or note:
        return "done"
    return "pending"


def _wf_disabled(state: dict[str, Any], agent: str) -> bool:
    """True when the run's workflow snapshot marks `agent` disabled (workflow-builder), so the rail
    can show it `skipped` before it self-skips at runtime."""
    for s in (state.get("workflow_snapshot") or {}).get("steps") or []:
        if isinstance(s, dict) and s.get("agent") == agent:
            return not s.get("enabled", True)
    return False


def _run_stage_status(state: dict[str, Any], stage: str) -> str:
    ns = state.get(stage) or {}
    pending_trigger = state.get("pending_trigger")
    # A workflow-disabled agent that hasn't run/errored shows as skipped up front.
    if _wf_disabled(state, stage) and not ns.get("error") and not (
        ns.get("spec") or ns.get("doc")
    ):
        return "skipped"
    # Liveness overlays (decision #33): a stage actively executing shows `running`; an orphaned
    # (stalled) stage shows `failed` so the rail/panel make the dead agent obvious.
    if state.get("running_stage") == stage and not state.get("running_story"):
        return "running"
    if state.get("stalled") and state.get("stalled_stage") == stage and not state.get(
        "stalled_story"
    ):
        return "failed"
    if stage == "intake":
        if ns.get("error"):
            return "failed"
        if ns.get("note") or state.get("raw_issue"):
            return "done"
        return "running" if state.get("status") == "running" else "pending"
    if stage == "pm":
        if pending_trigger == "pm":
            return "awaiting"
        if state.get("pending_review"):
            return "awaiting"
        if ns.get("error"):
            return "failed"
        if ns.get("spec"):
            return "done"
        return "pending"
    # rfc
    if pending_trigger == "rfc":
        return "awaiting"
    if ns.get("error"):
        return "failed"
    if ns.get("doc"):
        return "done"
    if (ns.get("note") or "").startswith("skipped"):
        return "skipped"
    return "pending"


def _story_stage_status(state: dict[str, Any], story: dict[str, Any], stage: str) -> str:
    pending_trigger = state.get("pending_trigger")
    pending_story = state.get("pending_story")
    tid = story.get("ticket_id")
    sub = story.get(stage) or {}
    if _wf_disabled(state, stage) and not sub.get("error") and not any(
        sub.get(k) for k in _STAGE_OUTPUT_KEYS.get(stage, ())
    ):
        return "skipped"
    if state.get("running_stage") == stage and state.get("running_story") == tid:
        return "running"
    if state.get("stalled") and state.get("stalled_stage") == stage and state.get(
        "stalled_story"
    ) == tid:
        return "failed"
    if pending_trigger == stage and pending_story == story.get("ticket_id"):
        return "awaiting"
    if stage == "reviewer" and state.get("pending_merge") and pending_story == story.get(
        "ticket_id"
    ):
        return "awaiting"
    return _ns_status(story.get(stage) or {}, _STAGE_OUTPUT_KEYS.get(stage, ()))


def _cockpit_stages(
    state: dict[str, Any], story: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Ordered stage descriptors for the pipeline rail."""
    by_story = (state.get("_metrics") or {}).get("by_story") or {}
    sid = (story or {}).get("ticket_id", "")
    out: list[dict[str, Any]] = []
    for name in _ALL_STAGES:
        scoped = name in _BUILD_STAGES
        if scoped:
            status = _story_stage_status(state, story or {}, name) if story else "pending"
            toks = (by_story.get(sid, {}).get(name) or {}) if sid else {}
        else:
            status = _run_stage_status(state, name)
            toks = (state.get("_run_level_tokens") or {}).get(name, {})
        out.append(
            {
                "name": name,
                "label": _STAGE_LABELS[name],
                "icon": _STAGE_ICONS[name],
                "status": status,
                "scoped": scoped,
                "tokens": toks,
            }
        )
    return out


def _default_stage(stages: list[dict[str, Any]]) -> str:
    """Pick the stage to show by default: the awaiting/running one, else the last done, else pm."""
    for s in stages:
        if s["status"] in ("awaiting", "running"):
            return s["name"]
    done = [s["name"] for s in stages if s["status"] in ("done", "failed")]
    return done[-1] if done else "pm"


async def _cockpit_ctx(
    run_id: str, state: dict[str, Any] | None, *, stage: str | None, story: str | None
) -> dict[str, Any]:
    state = state or {}
    metrics = await _run_metrics(run_id)
    state["_metrics"] = metrics
    # Run-level token attribution (pm/rfc/intake live under ticket "" in the breakdown).
    state["_run_level_tokens"] = (metrics.get("by_story") or {}).get("", {})
    # Liveness: mark the running stage, or flag a stalled (orphaned) run (decision #33 follow-up).
    await _augment_liveness(run_id, state)
    project = state.get("project", "")
    triggers = await _agent_triggers(project)
    enabled = await _agent_enabled(project)
    ids = _story_ids(state)
    sid = story if story in ids else (state.get("current_story") or (ids[0] if ids else ""))
    stories = state.get("stories") or {}
    sel_story = stories.get(sid) if sid else None
    stages = _cockpit_stages(state, sel_story)
    selected = stage if stage in _ALL_STAGES else _default_stage(stages)
    status = state.get("status", "running")
    return {
        "run_id": run_id,
        "state": state,
        "stages": stages,
        "selected": selected,
        "story_ids": ids,
        "selected_story": sid,
        "stories": stories,
        "metrics": metrics,
        "live_stream": status == "running" and not state.get("stalled"),
        "triggers": triggers,
        "agent_enabled": enabled,
        "rfc_enabled": enabled.get("rfc", True),
        "build_stages": _BUILD_STAGES,
        "stage_labels": _STAGE_LABELS,
    }


@router.get("/ui/run/{run_id}/events")
async def run_cockpit_events(
    request: Request, run_id: str, stage: str = Query(default=""), story: str = Query(default="")
) -> StreamingResponse:
    """Stream the cockpit body (rail + selected panel) until terminal/awaiting state."""
    runner = _runner(request)
    body = templates.get_template("_cockpit_body.html")

    async def gen() -> AsyncIterator[str]:
        for _ in range(800):
            if await request.is_disconnected():
                return
            state = await runner.get_run(run_id) or {}
            ctx = await _cockpit_ctx(run_id, state, stage=stage or None, story=story or None)
            yield _sse(body.render(**ctx), event="message")
            st = state.get("status")
            # `_cockpit_ctx` sets state["stalled"] when the run is orphaned (server restarted
            # mid-run): stop streaming so the UI settles on the stalled banner instead of polling
            # a run that nothing is driving.
            if (
                st in _TERMINAL
                or st in ("awaiting_review", "awaiting_trigger", "awaiting_merge")
                or state.get("stalled")
            ):
                yield _sse("", event="done")
                return
            await asyncio.sleep(1.5)
        yield _sse("", event="done")

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/ui/run/{run_id}", response_class=HTMLResponse)
async def run_cockpit(
    request: Request, run_id: str, story: str = Query(default="")
) -> HTMLResponse:
    state = await _runner(request).get_run(run_id)
    ctx = await _cockpit_ctx(run_id, state, stage=None, story=story or None)
    return templates.TemplateResponse(request, "run_cockpit.html", ctx)


@router.get("/ui/run/{run_id}/io", response_class=HTMLResponse)
async def run_cockpit_io(
    request: Request, run_id: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    """Per-run LLM I/O, scoped to this run (delegates to the shared I/O view)."""
    return await _io_view(request, session, run_id=run_id, back_to=f"/ui/run/{run_id}")


@router.get("/ui/run/{run_id}/{stage}", response_class=HTMLResponse)
async def run_cockpit_stage(
    request: Request, run_id: str, stage: str, story: str = Query(default="")
) -> HTMLResponse:
    if stage not in _ALL_STAGES:
        return RedirectResponse(url=f"/ui/run/{run_id}", status_code=303)  # type: ignore[return-value]
    state = await _runner(request).get_run(run_id)
    ctx = await _cockpit_ctx(run_id, state, stage=stage, story=story or None)
    return templates.TemplateResponse(request, "run_cockpit.html", ctx)


# ── Cockpit actions (consolidated) ─────────────────────────────────────────────


def _back_to_stage(run_id: str, stage: str, story: str = "") -> RedirectResponse:
    url = f"/ui/run/{run_id}/{stage or 'pm'}"
    if story:
        url += f"?story={story}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/ui/run/{run_id}/trigger")
async def cockpit_trigger(
    request: Request, run_id: str, stage: str = Form(default=""), story: str = Form(default="")
) -> RedirectResponse:
    """Fire a manual-trigger gate (resume the paused agent)."""
    runner = _runner(request)
    state = await runner.get_run(run_id)
    pending_trigger = (state or {}).get("pending_trigger")
    task_running = (state or {}).get("_task_running")
    logger.info(
        "[cockpit_trigger] run=%s stage=%s story=%s pending_trigger=%s task_running=%s",
        run_id[:8], stage, story, pending_trigger, task_running,
    )
    if state:
        try:
            snap = await runner._graph.aget_state({"configurable": {"thread_id": run_id}})
            interrupts = [i.value for i in (snap.interrupts or [])]
            next_nodes = list(getattr(snap, "next", None) or [])
            logger.info("[cockpit_trigger] snapshot interrupts=%s next=%s", interrupts, next_nodes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cockpit_trigger] could not read snapshot: %s", exc)
    await runner.resume_run(run_id, "run", background=True)
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/skip")
async def cockpit_skip(
    request: Request, run_id: str, stage: str = Form(default=""), story: str = Form(default="")
) -> RedirectResponse:
    """Skip a manual-trigger gate (resume with a non-run decision → agent self-skips)."""
    await _runner(request).resume_run(run_id, "skip", background=True)
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/stop")
async def cockpit_stop(
    request: Request, run_id: str, stage: str = Form(default=""), story: str = Form(default="")
) -> RedirectResponse:
    await _runner(request).stop_run(run_id)
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/restart")
async def cockpit_restart(
    request: Request, run_id: str, stage: str = Form(default=""), story: str = Form(default="")
) -> RedirectResponse:
    await _runner(request).resume_stopped(run_id)
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/retry")
async def cockpit_retry(
    request: Request,
    run_id: str,
    stage: str = Form(default=""),
    story: str = Form(default=""),
    from_step: str = Form(default=""),
) -> RedirectResponse:
    tid = story or None
    step = from_step or (stage if stage in _BUILD_STAGES else None)
    await _runner(request).retry_run(run_id, ticket_id=tid, from_step=step)
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/approve")
async def cockpit_approve(
    request: Request,
    run_id: str,
    stage: str = Form(default=""),
    story: str = Form(default=""),
    stories: list[str] = Form(default=[]),  # noqa: B008
    next_action: str = Form(default="build"),
    pr_strategy: str = Form(default=""),
) -> RedirectResponse:
    """Approve a spec-review or merge gate. For the spec gate, optionally select stories + the
    follow-up (build/rfc); for a completed pm_only run, kick off building from the spec."""
    from ash.observability import langsmith as _ls

    runner = _runner(request)
    _ls.score(run_id, "hitl_decision", 1.0, comment="Approved by user (cockpit)")
    state = await runner.get_run(run_id)
    chosen = [s for s in stories if s.strip()]
    # F7: let the client choose combined vs per-story PR at the gate (patched before resume).
    if pr_strategy in ("per_story", "single"):
        await runner.set_pr_strategy(run_id, pr_strategy)
    if state and state.get("pending_merge"):
        await runner.resume_run(run_id, "approve", background=True)
    elif state and state.get("pending_review"):
        decision: Any = {"action": "approve", "next": next_action or "build"}
        if chosen:
            decision["stories"] = chosen
        await runner.resume_run(run_id, decision, background=True)
    else:
        await runner.build_from_spec(run_id, story_selection=chosen or None)
    return _back_to_stage(run_id, stage or "dev", story)


@router.post("/ui/run/{run_id}/reject")
async def cockpit_reject(
    request: Request, run_id: str, stage: str = Form(default=""), story: str = Form(default="")
) -> RedirectResponse:
    from ash.observability import langsmith as _ls

    _ls.score(run_id, "hitl_decision", -1.0, comment="Rejected by user (cockpit)")
    await _runner(request).resume_run(run_id, "reject", background=True)
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/refine")
async def cockpit_refine(
    request: Request,
    run_id: str,
    stage: str = Form(...),
    story: str = Form(default=""),
    feedback: str = Form(default=""),
    custom_prompt: str = Form(default=""),
) -> RedirectResponse:
    """Per-agent (and per-story) HITL feedback re-run."""
    if stage == "pm" and story:
        # PM per-ticket refine keeps the spec gate open (in-place ticket edit).
        await _runner(request).refine_ticket(run_id, ticket_id=story, feedback=feedback)
    else:
        await _runner(request).refine_agent(
            run_id, agent=stage, ticket_id=story or None,
            feedback=feedback, custom_prompt=custom_prompt,
        )
    return _back_to_stage(run_id, stage, story)


@router.post("/ui/run/{run_id}/retrigger")
async def cockpit_retrigger(
    request: Request,
    run_id: str,
    stage: str = Form(...),
    story: str = Form(default=""),
    custom_prompt: str = Form(default=""),
) -> RedirectResponse:
    """Re-run an agent for a better result, with an optional custom prompt."""
    logger.info("[cockpit_retrigger] run=%s stage=%s story=%s", run_id[:8], stage, story)
    await _runner(request).retrigger_agent(
        run_id, agent=stage, ticket_id=story or None, custom_prompt=custom_prompt
    )
    return _back_to_stage(run_id, stage, story)


@router.get("/ui/io", response_class=HTMLResponse)
async def global_io(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: str = Query(default=""),
    ticket: str = Query(default=""),
    phase: str = Query(default=""),
    query: str = Query(default=""),
) -> HTMLResponse:
    """Global LLM-I/O log across all runs (decision #33 / Phase D)."""
    return await _io_view(
        request, session, agent=agent, ticket=ticket, phase=phase, query=query
    )


# ── U3: agents overview ──────────────────────────────────────────────────────


async def _agent_rows(project_name: str, session: AsyncSession) -> list[dict[str, Any]]:
    """Per-agent display rows using DB-resolved policy (DB > YAML > default)."""
    from ash.db.tasks import resolve_policy

    settings = get_settings()
    try:
        project = load_project(project_name)
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for name in KNOWN_AGENTS:
        policy = await resolve_policy(session, project, name)
        built = name in BUILT_AGENTS
        rows.append(
            {
                "name": name,
                "built": built,
                "trigger": policy.trigger,
                "enabled": policy.enabled,
                "model": settings.model_for(name).model if built else "—",
                "hitl": (
                    project.autonomy.require_human_for_merge if name == "reviewer" else None
                ),
            }
        )
    return rows


@router.get("/ui/agents", response_class=HTMLResponse)
async def agents_view(
    request: Request,
    project: str = Query(default=""),
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> HTMLResponse:
    from ash.db.metrics import agent_rollup
    from ash.db.tasks import task_stats

    projects = _projects()
    selected = project or (projects[0] if projects else "")
    rows = await _agent_rows(selected, session)
    # Analytics rollup (F8): tokens + time per agent across runs.
    try:
        rollup = {r["agent_name"]: r for r in await agent_rollup(session, project=selected)}
    except Exception:  # noqa: BLE001 — analytics are best-effort
        rollup = {}
    for row in rows:
        s = await task_stats(session, row["name"], selected)
        row["pending_count"] = s.get("pending", 0)
        row["in_progress_count"] = s.get("in_progress", 0)
        row["metrics"] = rollup.get(row["name"], {})
    return templates.TemplateResponse(
        request,
        "agents.html",
        {"agents": rows, "projects": projects, "selected": selected},
    )


# ── U4: per-agent detail page ────────────────────────────────────────────────


@router.get("/ui/agents/{agent_name}", response_class=HTMLResponse)
async def agent_detail_view(
    request: Request,
    agent_name: str,
    project: str = Query(default=""),
    status: str = Query(default=""),
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> HTMLResponse:
    from ash.db.tasks import list_tasks, resolve_policy, task_stats

    projects = _projects()
    selected = project or (projects[0] if projects else "")
    settings = get_settings()

    # Build policy (DB override → YAML → default). Must resolve against the DB so the
    # edit form reflects what was actually saved, not just the static YAML.
    try:
        proj = load_project(selected)
        policy = await resolve_policy(session, proj, agent_name)
    except FileNotFoundError:
        proj = None
        policy = None

    # Tasks kanban data
    statuses = ["pending", "in_progress", "completed", "failed", "cancelled"]
    filter_status = status if status in statuses else None
    tasks = await list_tasks(
        session,
        agent_name,
        selected,
        status=filter_status,
        limit=100,
    )
    stats = await task_stats(session, agent_name, selected)

    # Recent runs for the "Assign task" modal
    recent_runs = list(
        (
            await session.execute(
                select(RunRecord)
                .where(RunRecord.project == selected)
                .order_by(RunRecord.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "agent_detail.html",
        {
            "agent_name": agent_name,
            "projects": projects,
            "selected": selected,
            "policy": policy,
            "tasks": tasks,
            "stats": stats,
            "filter_status": filter_status,
            "statuses": statuses,
            "model": settings.model_for(agent_name).model if agent_name in BUILT_AGENTS else "—",
            "built": agent_name in BUILT_AGENTS,
            "recent_runs": recent_runs,
        },
    )


@router.post("/ui/agents/{agent_name}/config", response_class=HTMLResponse)
async def agent_config_save(
    request: Request,
    agent_name: str,
    project: str = Form(...),
    trigger: str = Form(default="auto"),
    enabled: str = Form(default=""),
    concurrency_limit: int = Form(default=1),
    daily_quota: str = Form(default=""),
    max_retries: int = Form(default=0),
    schedule_cron: str = Form(default=""),
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> RedirectResponse:
    from ash.db.tasks import upsert_policy_override

    await upsert_policy_override(
        session,
        project=project,
        agent_name=agent_name,
        trigger=trigger if trigger in ("auto", "manual") else "auto",
        enabled=enabled == "on",
        concurrency_limit=max(1, concurrency_limit),
        daily_quota=int(daily_quota) if daily_quota.strip() else None,
        max_retries=max(0, max_retries),
        schedule_cron=schedule_cron.strip() or None,
    )
    await session.commit()
    return RedirectResponse(
        f"/ui/agents/{agent_name}?project={project}", status_code=303
    )


@router.post("/ui/agents/{agent_name}/config/reset", response_class=HTMLResponse)
async def agent_config_reset(
    request: Request,
    agent_name: str,
    project: str = Form(...),
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> RedirectResponse:
    from ash.db.tasks import delete_policy_override

    await delete_policy_override(session, project=project, agent_name=agent_name)
    await session.commit()
    return RedirectResponse(
        f"/ui/agents/{agent_name}?project={project}", status_code=303
    )


@router.post("/ui/tasks/{task_id}/trigger", response_class=HTMLResponse)
async def task_trigger(
    request: Request,
    task_id: int,
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> RedirectResponse:
    """Manually trigger a pending task by resuming its run."""
    import asyncio as _asyncio

    from ash.db.models import AgentTask
    from ash.db.tasks import update_task_status

    task = await session.get(AgentTask, task_id)
    if task is None:
        return RedirectResponse("/ui/agents", status_code=303)

    runner = _runner(request)
    await update_task_status(session, task_id, "in_progress")
    await session.commit()
    _asyncio.create_task(runner.resume_run(task.run_id, "run"))
    return RedirectResponse(
        f"/ui/agents/{task.agent_name}?project={task.project}", status_code=303
    )


@router.post("/ui/tasks/{task_id}/cancel", response_class=HTMLResponse)
async def task_cancel(
    request: Request,
    task_id: int,
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> RedirectResponse:
    from ash.db.models import AgentTask
    from ash.db.tasks import update_task_status

    task = await session.get(AgentTask, task_id)
    if task is None:
        return RedirectResponse("/ui/agents", status_code=303)

    await update_task_status(session, task_id, "cancelled")
    await session.commit()
    return RedirectResponse(
        f"/ui/agents/{task.agent_name}?project={task.project}", status_code=303
    )


@router.post("/ui/tasks/assign", response_class=HTMLResponse)
async def task_assign(
    request: Request,
    agent_name: str = Form(...),
    project: str = Form(...),
    run_id: str = Form(...),
    title: str = Form(default=""),
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> RedirectResponse:
    """Manually create (or surface) an AgentTask for a given run × agent.

    Useful when a task was not auto-created, or to schedule future work on a paused run.
    The task lands in 'pending' so the user can Trigger it (or the dispatcher picks it up).
    """
    from ash.db.tasks import upsert_agent_task

    # Resolve item_id + a sensible title from the RunRecord
    run_rec = await session.get(RunRecord, run_id)
    item_id = run_rec.item_id if run_rec else run_id[:8]
    resolved_title = title.strip() or (
        f"{run_rec.item_id}" if run_rec else run_id[:8]
    )

    await upsert_agent_task(
        session,
        agent_name=agent_name,
        project=project,
        run_id=run_id,
        item_id=item_id,
        title=resolved_title,
        status="pending",
    )
    await session.commit()
    return RedirectResponse(
        f"/ui/agents/{agent_name}?project={project}", status_code=303
    )
