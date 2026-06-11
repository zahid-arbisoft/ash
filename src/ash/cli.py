"""Thin CLI for local runs without the API.

`ash list --project plane` lists issues; `ash run --project plane --issue 42` runs the full graph
once with an in-memory checkpointer (no Postgres needed) and prints the final state. The FastAPI
app (`ash.api.app:app`) is the production entrypoint.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

import httpx
from langgraph.checkpoint.memory import MemorySaver

from ash.app_context import build_runner
from ash.clients.github import GitHubClient
from ash.config.settings import get_settings, load_project


async def _list(project_name: str, limit: int) -> int:
    project = load_project(project_name)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as http:
        gh = GitHubClient(token=settings.github_token, repo=project.issues.source_repo, http=http)
        issues = await gh.list_issues(filters=project.issues.filters, limit=limit)
    print(f"Open issues in {project.issues.source_repo}:\n")
    for it in issues:
        labels = f"  [{', '.join(it.labels)}]" if it.labels else ""
        print(f"  #{it.number}  {it.title}{labels}")
    return 0


async def _run(project_name: str, issue: int) -> int:
    settings = get_settings()
    runner = build_runner(settings, checkpointer=MemorySaver())
    run_id = await runner.start_run(project=project_name, item_id=str(issue), wait=True)
    state = await runner.get_run(run_id)
    print(json.dumps(state, default=str, indent=2))
    return 0


async def _create_admin(username: str, password: str) -> int:
    from ash.admin.users import create_or_update_admin
    from ash.db.base import get_sessionmaker, init_db

    await init_db()
    async with get_sessionmaker()() as session:
        await create_or_update_admin(session, username=username, password=password)
    print(f"admin user '{username}' created/updated")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ash", description="ASH — Agentic Software House engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list open issues from the project's source repo")
    p_list.add_argument("--project", required=True)
    p_list.add_argument("--limit", type=int, default=20)

    p_run = sub.add_parser("run", help="run the full graph once for one issue (in-memory)")
    p_run.add_argument("--project", required=True)
    p_run.add_argument("--issue", type=int, required=True)

    p_admin = sub.add_parser("create-admin", help="create (or reset) an admin-portal user")
    p_admin.add_argument("--username", required=True)
    p_admin.add_argument("--password", default=None, help="omit to be prompted securely")

    args = parser.parse_args(argv)
    if args.command == "list":
        return asyncio.run(_list(args.project, args.limit))
    if args.command == "run":
        return asyncio.run(_run(args.project, args.issue))
    if args.command == "create-admin":
        password = args.password or getpass.getpass("Password: ")
        if not password:
            parser.error("password must not be empty")
        return asyncio.run(_create_admin(args.username, password))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
