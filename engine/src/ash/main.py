"""Phase 0 CLI: list issues from a project's source repo, and build a spec for one issue.

python -m ash.main list --project plane
python -m ash.main spec --project plane --issue 1234
"""

from __future__ import annotations

import argparse
import sys

from .agents.pm_agent import build_spec
from .config import load_llm_settings, load_project
from .connectors.github import fetch_issue, list_issues
from .llm import LLMClient
from .pipeline import run_build


def _cmd_list(args) -> int:
    project = load_project(args.project)
    issues = list_issues(project.issues.source_repo, project.issues.filters, limit=args.limit)
    print(
        f"Open issues in {project.issues.source_repo} (filters={project.issues.filters or '{}'}):\n"
    )
    for it in issues:
        labels = f"  [{', '.join(it.labels)}]" if it.labels else ""
        print(f"  #{it.number}  {it.title}{labels}")
    return 0


def _cmd_spec(args) -> int:
    project = load_project(args.project)
    settings = load_llm_settings()
    if not settings.api_key and not settings.base_url:
        print(
            "error: no LLM credentials. Set LLM_API_KEY (and optionally LLM_BASE_URL) in .env",
            file=sys.stderr,
        )
        return 2

    repo = project.issues.source_repo
    print(f"Fetching {repo}#{args.issue} ...", file=sys.stderr)
    issue = fetch_issue(repo, args.issue)

    print(f"Running PM Agent ({settings.provider}:{settings.model_for('pm')}) ...", file=sys.stderr)
    result = build_spec(LLMClient(settings), repo, issue)

    out_dir = project.runtime_dir / "specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"issue-{args.issue}.json"
    out_path.write_text(result.parsed.model_dump_json(indent=2))

    print(result.parsed.model_dump_json(indent=2))
    print(
        f"\nsaved -> {out_path}\n"
        f"tokens in/out: {result.usage.input_tokens}/{result.usage.output_tokens}",
        file=sys.stderr,
    )
    return 0


def _cmd_build(args) -> int:
    project = load_project(args.project)
    settings = load_llm_settings()
    if not settings.api_key and not settings.base_url:
        print(
            "error: no LLM credentials. Set LLM_API_KEY (and optionally LLM_BASE_URL)",
            file=sys.stderr,
        )
        return 2
    if not project.work.resolved_local_path():
        print(
            "error: no local clone configured. Set work.local_repo_path in "
            f"projects/{args.project}.yaml or LOCAL_REPO_PATH in .env",
            file=sys.stderr,
        )
        return 2

    print(f"Building issue #{args.issue} for project '{project.name}' ...", file=sys.stderr)
    state = run_build(project, args.issue, keep_worktree=args.keep)

    out_dir = project.runtime_dir / "state"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"issue-{args.issue}.json"
    out_path.write_text(state.model_dump_json(indent=2))

    print(f"\nbranch:  {state.branch}")
    print(f"PR:      {state.pr_url}")
    print(f"stage:   {state.stage.value} / {state.status.value}")
    print(f"state -> {out_path}", file=sys.stderr)
    return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ash", description="Loop engine (Phase 0)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list open issues from the project's source repo")
    p_list.add_argument("--project", required=True)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=_cmd_list)

    p_spec = sub.add_parser("spec", help="build a spec for one issue")
    p_spec.add_argument("--project", required=True)
    p_spec.add_argument("--issue", type=int, required=True)
    p_spec.set_defaults(func=_cmd_spec)

    p_build = sub.add_parser(
        "build", help="Phase 1 walking skeleton: issue -> spec -> branch -> PR"
    )
    p_build.add_argument("--project", required=True)
    p_build.add_argument("--issue", type=int, required=True)
    p_build.add_argument("--keep", action="store_true", help="keep the worktree after the run")
    p_build.set_defaults(func=_cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(cli())
