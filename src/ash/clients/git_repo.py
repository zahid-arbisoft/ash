"""Git workspace management — operates on an existing local clone of the work target (the fork).

Each ticket gets its own **git worktree** (plan's parallel-safety primitive) so concurrent tickets
never collide. We never touch the user's checked-out branch in the main clone.

Phase 1 walking skeleton uses: validate clone -> sync base -> add worktree+branch -> commit -> push.
PR creation lives in `pr.py` (gh CLI).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from git import Repo

from ..config import WorkTarget


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen].strip("-") or "ticket"


class RepoWorkspace:
    def __init__(self, work: WorkTarget, worktrees_root: Path, *, github_token: str = ""):
        path = work.resolved_local_path()
        if not path or not path.exists():
            raise FileNotFoundError(
                "No local clone found. Set work.local_repo_path in projects/<name>.yaml "
                "or LOCAL_REPO_PATH in .env to an existing clone of "
                f"{work.target_repo}."
            )
        self._github_token = github_token
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
        wt_name = wt_path.name  # matches the subdir name under .git/worktrees/

        # ── 1. Remove stale worktree directory ──────────────────────────────
        if wt_path.exists():
            try:
                self.repo.git.worktree("remove", "--force", str(wt_path))
            except Exception:  # noqa: BLE001
                shutil.rmtree(wt_path, ignore_errors=True)

        # ── 2. Remove stale git-internal worktree metadata ──────────────────
        # git keeps a lock entry at .git/worktrees/<wt_name>/ even after the
        # directory is gone. While that entry exists, git refuses to delete the
        # branch (it considers it "checked out"). Removing it directly is the
        # only reliable way to unblock `git branch -D`.
        git_wt_meta = Path(str(self.repo.git_dir)) / "worktrees" / wt_name
        if git_wt_meta.exists():
            shutil.rmtree(git_wt_meta, ignore_errors=True)

        try:
            self.repo.git.worktree("prune")
        except Exception:  # noqa: BLE001
            pass

        # ── 3. Delete the stale branch ───────────────────────────────────────
        try:
            self.repo.git.branch("-D", branch)
        except Exception:  # noqa: BLE001
            pass

        # ── 4. Create fresh worktree ─────────────────────────────────────────
        self.repo.git.worktree("add", "-b", branch, str(wt_path), base_ref)
        return wt_path

    def open_or_create_worktree(self, branch: str, base_ref: str) -> Path:
        """Reuse an existing worktree for `branch` if present, else create it fresh from `base_ref`.

        Used by the combined-PR strategy (F7): the first story creates the shared worktree; later
        stories reuse it (with all prior stories' commits intact) so their changes stack onto the
        same branch instead of resetting it. Falls back to `create_worktree` when nothing exists."""
        wt_path = self.worktrees_root / branch.replace("/", "__")
        if wt_path.exists() and (wt_path / ".git").exists():
            return wt_path
        return self.create_worktree(branch, base_ref)

    def remove_worktree(self, wt_path: Path, *, force: bool = True) -> None:
        args = ["remove", str(wt_path)]
        if force:
            args.append("--force")
        self.repo.git.worktree(*args)

    def branch_name(self, issue_number: int, title: str) -> str:
        return f"agent/issue-{issue_number}-{_slug(title)}"

    def branch_name_from(self, item_id: str, title: str) -> str:
        return f"agent/issue-{_slug(item_id)}-{_slug(title)}"

    # ── commit / push (operate inside the worktree) ─────────────────────────────

    def commit_all(self, wt_path: Path, message: str) -> str:
        wt = Repo(wt_path)
        wt.git.add(A=True)
        wt.index.commit(message)
        return wt.head.commit.hexsha

    def push_branch(self, wt_path: Path, branch: str, *, force: bool = False) -> None:
        wt = Repo(wt_path)
        # Embed token for headless HTTPS auth (Docker / CI — no interactive credential helper).
        push_url = (
            self.https_url.replace("https://", f"https://oauth2:{self._github_token}@")
            if self._github_token
            else self.https_url
        )
        refspec = f"{branch}:refs/heads/{branch}"
        # `agent/*` branches are bot-owned and regenerated per run. On a RE-RUN of the same issue
        # the remote branch still holds the previous attempt, while our fresh worktree branched
        # straight from base — so the two diverge and a normal push is rejected non-fast-forward.
        # force replaces the stale branch with the latest attempt. (The Fixer's same-run pushes
        # build on this branch and stay fast-forward, so force is a harmless no-op there.)
        if force:
            wt.git.push(push_url, refspec, force=True)
        else:
            wt.git.push(push_url, refspec)
