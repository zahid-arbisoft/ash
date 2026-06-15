"""Dev toolkit — sandboxed read/run tools for the CodingAgent and Fixer inner loop.

Wraps worktree-scoped file I/O and shell execution as LangChain `BaseTool`s so
`create_agent` can call them in its ReAct loop. Writes happen outside the toolkit
(via `apply_change` in `coding.py`) so we keep a clean separation: tools are
read + run; the orchestration layer applies `CodeChange` and commits.
"""

from __future__ import annotations

import glob
import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from ash.clients import code_intel

# Commands allowed by run_command — test/lint only, never git write or rm.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "pytest",
    "python -m pytest",
    "ruff",
    "mypy",
    "npm test",
    "npm run test",
    "yarn test",
    "pnpm test",
    "go test",
    "go vet",
    "make test",
    "make lint",
    "make check",
    "cargo test",
    "cargo clippy",
    "./gradlew test",
    "./mvnw test",
    "bundle exec rspec",
    "mix test",
)


class DevToolkit:
    """Sandboxed read + run tools for the Coding/Fixer agent loop.

    Parameters
    ----------
    worktree:    Path to the isolated git worktree for this ticket.
    allowed_cmd: If provided, only this command (or its prefix) is permitted by
                 `run_command`. Falls back to the full ``_ALLOWED_PREFIXES`` list.
    """

    def __init__(self, *, worktree: Path, allowed_cmd: str | None = None) -> None:
        self._worktree = worktree
        self._allowed_cmd = allowed_cmd

    def get_tools(self) -> list[BaseTool]:
        root = self._worktree
        allowed = self._allowed_cmd

        def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
            """Read a source file by path relative to the repo root.

            Optionally pass start_line/end_line (1-based, inclusive) to read only that span
            instead of the whole file — keeps only the relevant code in context."""
            return (
                code_intel.read_file(
                    root, path, start_line=start_line or None, end_line=end_line or None
                )
                or "(file does not exist)"
            )

        def list_files(pattern: str) -> str:
            """List files matching a glob pattern (relative to repo root, supports **)."""
            try:
                matches = sorted(
                    glob.glob(pattern, root_dir=str(root), recursive=True)
                )
                return "\n".join(matches[:80]) or "(no matches)"
            except Exception as exc:  # noqa: BLE001
                return f"(error: {exc})"

        def search_code(query: str) -> str:
            """Search the repo for a string. Returns path:line:match hits."""
            hits = code_intel.search(root, query)
            return "\n".join(hits) or "(no results)"

        allowed_label = allowed or "any allowed test/lint command"

        def run_command(cmd: str) -> str:
            """Run a test or lint command inside the repo root (max 120s)."""
            stripped = cmd.strip()
            if allowed:
                # When a specific command was detected, only allow that prefix.
                if not stripped.startswith(allowed.split()[0]):
                    return (
                        f"[blocked] Only '{allowed}' is permitted here. "
                        f"Received: '{stripped[:80]}'"
                    )
            # Fallback: must match one of the well-known safe prefixes.
            if not any(stripped.startswith(p) for p in _ALLOWED_PREFIXES):
                return (
                    "[blocked] Command not in the allow-list (test/lint only). "
                    f"Received: '{stripped[:80]}'"
                )
            try:
                proc = subprocess.run(
                    stripped,
                    shell=True,  # noqa: S602 — sandboxed by allow-list above
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(root),
                )
                out = (proc.stdout + proc.stderr)[:4000]
                return f"exit={proc.returncode}\n{out}"
            except subprocess.TimeoutExpired:
                return "exit=timeout (120s limit exceeded)"
            except Exception as exc:  # noqa: BLE001
                return f"exit=error: {exc}"

        return [
            StructuredTool.from_function(
                func=read_file,
                name="read_file",
                description=(
                    "Read a source file by path relative to the repo root. "
                    "Use this before editing to understand the current code."
                ),
            ),
            StructuredTool.from_function(
                func=list_files,
                name="list_files",
                description=(
                    "List files matching a glob pattern relative to the repo root. "
                    "Supports ** for recursive matching (e.g. 'src/**/*.py')."
                ),
            ),
            StructuredTool.from_function(
                func=search_code,
                name="search_code",
                description=(
                    "Search the codebase for a string or pattern. "
                    "Returns 'path:line:match' hits. Useful for finding usages."
                ),
            ),
            StructuredTool.from_function(
                func=run_command,
                name="run_command",
                description=(
                    "Run a test or lint command in the repo root (120s timeout). "
                    f"Allowed: {allowed_label}. "
                    "Returns the exit code and the last ~4000 chars of output."
                ),
            ),
        ]
