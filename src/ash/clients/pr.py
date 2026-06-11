"""Pull-request creation via the `gh` CLI (already authenticated, so no token needed in .env).

Fork-internal only for now: head and base both live in the target repo. We never open PRs against
upstream while building/testing (plan decision #5).
"""

from __future__ import annotations

import shutil
import subprocess


class GhNotAvailable(RuntimeError):
    pass


def _gh() -> str:
    path = shutil.which("gh")
    if not path:
        raise GhNotAvailable("the GitHub CLI `gh` is not installed / not on PATH")
    return path


def create_pr(
    *, target_repo: str, base: str, head: str, title: str, body: str, draft: bool = True
) -> str:
    """Open a PR in target_repo (head -> base). Returns the PR URL."""
    cmd = [
        _gh(),
        "pr",
        "create",
        "--repo",
        target_repo,
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        cmd.append("--draft")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr create failed:\n{result.stderr.strip()}")
    return result.stdout.strip()
