"""Read-only code intelligence over a checked-out worktree.

Grounds the Research agent in the *actual* repo (not the model's imagination). All operations are
sandboxed to the worktree root; nothing here writes. Uses ripgrep when available, else a pure-Python
fallback so it works everywhere.
"""

from __future__ import annotations

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


def read_file(root: Path, rel_path: str, max_chars: int = 6000) -> str:
    """Read a file inside the worktree (truncated). Refuses to escape the root."""
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise ValueError(f"path escapes worktree: {rel_path}")
    if not target.is_file():
        return ""
    text = target.read_text(errors="ignore")
    return text[:max_chars] + ("\n…(truncated)…" if len(text) > max_chars else "")
