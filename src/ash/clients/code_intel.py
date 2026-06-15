"""Read-only code intelligence over a checked-out worktree.

Grounds the Research agent in the *actual* repo (not the model's imagination). All operations are
sandboxed to the worktree root; nothing here writes. Uses ripgrep when available, else a pure-Python
fallback so it works everywhere.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

_IGNORE_DIRS = {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}


def repo_tree(root: Path, max_depth: int = 2, max_entries: int = 200) -> str:
    """A shallow directory overview to orient the agent."""
    lines: list[str] = []
    root = root.resolve()

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth or len(lines) >= max_entries:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return
        for p in entries:
            if p.name in _IGNORE_DIRS or p.name.startswith("."):
                continue
            if len(lines) >= max_entries:
                return
            rel = p.relative_to(root)
            lines.append(f"{'  ' * depth}{rel}{'/' if p.is_dir() else ''}")
            if p.is_dir():
                walk(p, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


def search(root: Path, query: str, max_results: int = 40) -> list[str]:
    """Return 'path:line:match' hits for a query string."""
    root = root.resolve()
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--no-heading", "--line-number", "-i", "-m", "3", query, str(root)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        rels = []
        for line in out.stdout.splitlines()[:max_results]:
            rels.append(line.replace(f"{root}/", "", 1))
        return rels
    # fallback: naive scan (bounded)
    hits: list[str] = []
    for p in root.rglob("*"):
        if len(hits) >= max_results:
            break
        if not p.is_file() or any(part in _IGNORE_DIRS for part in p.parts):
            continue
        try:
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if query.lower() in line.lower():
                    hits.append(f"{p.relative_to(root)}:{i}:{line.strip()[:160]}")
                    break
        except OSError:
            continue
    return hits


def read_file(
    root: Path,
    rel_path: str,
    max_chars: int = 6000,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read a file inside the worktree. Refuses to escape the root.

    When `start_line`/`end_line` (1-based, inclusive) are given, returns only that span — so the
    agent reads exactly the relevant lines (e.g. from a search hit) instead of the file head
    (decision #26 / F7). Each returned line is numbered for easy reference.
    """
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise ValueError(f"path escapes worktree: {rel_path}")
    if not target.is_file():
        return ""
    text = target.read_text(errors="ignore")
    if start_line is not None or end_line is not None:
        lines = text.splitlines()
        lo = max(1, start_line or 1)
        hi = min(len(lines), end_line or len(lines))
        if lo > hi:
            return ""
        span = lines[lo - 1 : hi]
        numbered = "\n".join(f"{lo + i}: {ln}" for i, ln in enumerate(span))
        return numbered[:max_chars] + ("\n…(truncated)…" if len(numbered) > max_chars else "")
    return text[:max_chars] + ("\n…(truncated)…" if len(text) > max_chars else "")


# ── Project convention detection ─────────────────────────────────────────────


def detect_test_command(root: Path) -> str | None:
    """Infer the project's test command from repo artefacts (no subprocess needed)."""
    # Python: any pytest config present → use pytest
    for marker in ("pytest.ini", "setup.cfg", "pyproject.toml"):
        if (root / marker).exists():
            return "python -m pytest"
    # Node / JS
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
            if "test" in scripts:
                return "npm test"
        except Exception:  # noqa: BLE001
            pass
    # Makefile with a "test" target
    makefile = root / "Makefile"
    if makefile.exists() and "test:" in makefile.read_text(errors="ignore"):
        return "make test"
    # Go
    if (root / "go.mod").exists():
        return "go test ./..."
    # Rust
    if (root / "Cargo.toml").exists():
        return "cargo test"
    # Ruby
    if (root / "Gemfile").exists():
        return "bundle exec rspec"
    # Elixir
    if (root / "mix.exs").exists():
        return "mix test"
    return None


def detect_commit_convention(root: Path) -> str:
    """Return a brief instruction for the commit message style used by this repo."""
    # Explicit config beats heuristics
    for cfg in (
        ".commitlintrc",
        ".commitlintrc.json",
        ".commitlintrc.yml",
        ".commitlintrc.yaml",
        "commitlint.config.js",
        "commitlint.config.ts",
    ):
        if (root / cfg).exists():
            return "Use Conventional Commits: type(scope): description (e.g. feat: add X)"
    # CONTRIBUTING mention
    for contrib in ("CONTRIBUTING.md", "CONTRIBUTING", "CONTRIBUTING.rst"):
        p = root / contrib
        if p.exists():
            text = p.read_text(errors="ignore")[:3000].lower()
            if "conventional commit" in text or "commitlint" in text:
                return "Use Conventional Commits format as described in CONTRIBUTING"
    # Infer from recent git log (majority vote)
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%s", "-30"],  # noqa: S603,S607
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        msgs = [m for m in proc.stdout.splitlines() if m.strip()]
        if msgs:
            conventional = sum(
                1
                for m in msgs
                if re.match(r"^(feat|fix|chore|docs|style|refactor|perf|test|build|ci)[\(:]", m)
            )
            if conventional > len(msgs) // 2:
                return (
                    "Use Conventional Commits (repo uses this): "
                    "type(scope): description — e.g. fix(api): correct response code"
                )
    except Exception:  # noqa: BLE001
        pass
    return (
        "Use clear imperative commit messages: '<verb> <what>' "
        "(e.g. 'add rate-limit middleware')"
    )


def read_pr_template(root: Path) -> str | None:
    """Return the repo's pull-request template text, or None if absent."""
    candidates = (
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/PULL_REQUEST_TEMPLATE",
        ".github/pull_request_template.md",
        "PULL_REQUEST_TEMPLATE.md",
        "docs/pull_request_template.md",
    )
    for rel in candidates:
        p = root / rel
        if p.is_file():
            return p.read_text(errors="ignore")[:4000]
    return None
