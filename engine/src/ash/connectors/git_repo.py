"""Git workspace management — operates on an existing local clone of the work target (the fork).

Each ticket gets its own **git worktree** (plan's parallel-safety primitive) so concurrent tickets
never collide. We never touch the user's checked-out branch in the main clone.

Phase 1 walking skeleton uses: validate clone -> sync base -> add worktree+branch -> commit -> push.
PR creation lives in `pr.py` (gh CLI).
"""

from __future__ import annotations

import re
from pathlib import Path

from git import Repo

from ..config import WorkTarget


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen].strip("-") or "ticket"


class RepoWorkspace:
    def __init__(self, work: WorkTarget, worktrees_root: Path):
        path = work.resolved_local_path()
        if not path or not path.exists():
            raise FileNotFoundError(
                "No local clone found. Set work.local_repo_path in projects/<name>.yaml "
                "or LOCAL_REPO_PATH in .env to an existing clone of "
                f"{work.target_repo}."
            )
        self.work = work
        self.repo = Repo(path)
        if self.repo.bare:
            raise ValueError(f"{path} is a bare repo; need a normal working clone")
        self.root = path
        self.worktrees_root = worktrees_root
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        # We auth git over HTTPS via the gh credential helper (plan decision: git auth = HTTPS+gh),
        # independent of however the clone's origin remote is configured (e.g. SSH).
        self.https_url = f"https://github.com/{work.target_repo}.git"

    # ── remotes / sync ───────────────────────────────────────────────────────

    def ensure_upstream(self) -> None:
        """In fork mode, make sure an 'upstream' remote points at work.upstream_remote."""
        if self.work.mode != "fork" or not self.work.upstream_remote:
            return
        url = f"https://github.com/{self.work.upstream_remote}.git"
        names = {r.name for r in self.repo.remotes}
        if "upstream" not in names:
            self.repo.create_remote("upstream", url)

    def sync_base(self) -> str:
        """Fetch the base branch over HTTPS into origin/<base>; return that ref to branch from."""
        base = self.work.base_branch
        self.repo.git.fetch(self.https_url, f"+refs/heads/{base}:refs/remotes/origin/{base}")
        return f"origin/{base}"

    # ── worktrees ──────────────────────────────────────────────────────────────

    def create_worktree(self, branch: str, base_ref: str) -> Path:
        wt_path = self.worktrees_root / branch.replace("/", "__")
        if wt_path.exists():
            raise FileExistsError(f"worktree already exists: {wt_path}")
        # git worktree add -b <branch> <path> <base_ref>
        self.repo.git.worktree("add", "-b", branch, str(wt_path), base_ref)
        return wt_path

    def remove_worktree(self, wt_path: Path, *, force: bool = True) -> None:
        args = ["remove", str(wt_path)]
        if force:
            args.append("--force")
        self.repo.git.worktree(*args)

    def branch_name(self, issue_number: int, title: str) -> str:
        return f"agent/issue-{issue_number}-{_slug(title)}"

    # ── commit / push (operate inside the worktree) ─────────────────────────────

    def commit_all(self, wt_path: Path, message: str) -> str:
        wt = Repo(wt_path)
        wt.git.add(A=True)
        wt.index.commit(message)
        return wt.head.commit.hexsha

    def push_branch(self, wt_path: Path, branch: str) -> None:
        wt = Repo(wt_path)
        wt.git.push(self.https_url, f"{branch}:refs/heads/{branch}")
