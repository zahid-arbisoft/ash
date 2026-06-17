"""Server-rendered UI routes (Jinja2). Configuration lives in the SQLAdmin portal at /admin."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

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
from ash.db.runs import search_spec_records
from ash.graph.runner import Runner

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

INTAKE_MODES = ["raw_to_spec", "spec_ready", "raw_to_dev"]
# agents wired into the graph (rfc is now real but opt-in via trigger=auto — plan §10.6)
BUILT_AGENTS = {"pm", "research", "coding", "reviewer", "fixer", "rfc"}


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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"connectors": connectors, "runs": runs, "week_metrics": week},
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
    return templates.TemplateResponse(
        request,
        "run_new.html",
        {
            "sources": sources,
            "sinks": sinks,
            "projects": _projects(),
            "modes": INTAKE_MODES,
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
    attachments: list[UploadFile] = File(default=[]),  # noqa: B008 — FastAPI form dependency
) -> RedirectResponse:
    int_id = int(integration_id) if integration_id else None
    sink_id = int(task_sink_id) if task_sink_id else None
    mode = story_mode if story_mode in ("single", "multiple") else "single"
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
        )
    )
    await session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


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


@router.get("/ui/runs/{run_id}", response_class=HTMLResponse)
async def run_status(request: Request, run_id: str) -> HTMLResponse:
    state = await _runner(request).get_run(run_id)
    ts = await _task_statuses(run_id)
    metrics = await _run_metrics(run_id)
    triggers = await _agent_triggers((state or {}).get("project", ""))
    return templates.TemplateResponse(
        request,
        "run_status.html",
        {
            "run_id": run_id,
            "state": state,
            "task_statuses": ts,
            "metrics": metrics,
            "triggers": triggers,
        },
    )


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
    chosen = [s for s in stories if s.strip()]
    decision: Any = {"action": "approve", "stories": chosen} if chosen else "approve"
    return await _decide(request, run_id, decision)


@router.post("/ui/runs/{run_id}/reject", response_class=HTMLResponse)
async def run_reject(request: Request, run_id: str) -> HTMLResponse:
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


_STORY_STEPS = ("research", "coding", "reviewer", "fixer")


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


@router.get("/ui/pm-runs/{run_id}", response_class=HTMLResponse)
async def pm_run_detail(
    request: Request, run_id: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> HTMLResponse:
    rec = await session.get(SpecRecord, run_id)
    return templates.TemplateResponse(request, "pm_run_detail.html", {"rec": rec, "run_id": run_id})


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
