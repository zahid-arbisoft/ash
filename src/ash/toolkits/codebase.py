"""Codebase toolkit — read-only repo intelligence (search/read) as LangChain tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from ash.clients import code_intel
from ash.clients.chroma import VectorStoreClient


class CodebaseToolkit:
    """Sandboxed, read-only search/read over a checked-out worktree.

    `client` is optional — when None (Chroma unavailable) the search_codebase tool
    falls back to a grep-based keyword search so the agent still has useful results.
    """

    def __init__(self, *, client: VectorStoreClient | None, root: Path) -> None:
        self._client = client
        self._root = root

    def get_tools(self) -> list[BaseTool]:
        def search_codebase(query: str, path: str = "", max_results: int = 10) -> str:
            """Search the codebase for relevant code. Returns file path and snippet per match.
            `path` optionally scopes the search to a subdirectory. `max_results` limits hits."""
            root = self._root
            if path:
                scoped = (root / path).resolve()
                if str(scoped).startswith(str(root.resolve())) and scoped.is_dir():
                    root = scoped
            n = max(1, min(max_results, 30))
            if self._client is not None:
                results = self._client.search(query, n_results=n)
                return "\n".join(results) or "(no results)"
            return _grep_search(root, query, max_results=n)

        def list_directory(path: str = "", depth: int = 2) -> str:
            """List the directory tree at the given path (relative to repo root).
            Also accepts the name 'print_tree' — same behaviour."""
            target = (self._root / path).resolve() if path else self._root.resolve()
            if not str(target).startswith(str(self._root.resolve())):
                return "(path escapes repo root)"
            if not target.is_dir():
                return "(not a directory)"
            return code_intel.repo_tree(target, max_depth=max(1, min(depth, 4)))

        def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
            """Read a file inside the repo by path relative to its root.

            Optionally pass start_line/end_line (1-based, inclusive) to read only that span —
            e.g. the line range from a search_codebase hit — instead of the whole file. This
            keeps only the relevant code in context."""
            return (
                code_intel.read_file(
                    self._root,
                    path,
                    start_line=start_line or None,
                    end_line=end_line or None,
                )
                or "(file does not exist)"
            )

        def grep_code(pattern: str, path: str = "", case_sensitive: bool = False) -> str:
            """Search the repo for a regex or literal string using grep. Returns path:line:match.
            `path` scopes the search to a subdirectory or file (relative to repo root).
            More precise than search_codebase for exact identifiers or import names."""
            root = self._root
            target = str((root / path).resolve()) if path else str(root.resolve())
            flags = ["-r", "-n", "--include=*.py", "--include=*.ts", "--include=*.js",
                     "--include=*.go", "--include=*.rs", "--include=*.yaml", "--include=*.toml"]
            if not case_sensitive:
                flags.append("-i")
            try:
                proc = subprocess.run(
                    ["grep", *flags, pattern, target],  # noqa: S603
                    capture_output=True, text=True, timeout=15, cwd=str(root)
                )
                lines = proc.stdout.splitlines()
                # Make paths relative to repo root for readability
                rel_lines = []
                for ln in lines[:60]:
                    try:
                        parts = ln.split(":", 2)
                        rel = str(Path(parts[0]).relative_to(root))
                        rel_lines.append(f"{rel}:{parts[1]}:{parts[2]}" if len(parts) == 3 else ln)
                    except ValueError:
                        rel_lines.append(ln)
                return "\n".join(rel_lines) or "(no matches)"
            except subprocess.TimeoutExpired:
                return "(timed out)"
            except FileNotFoundError:
                return "(grep not available)"

        return [
            StructuredTool.from_function(
                func=search_codebase,
                name="search_codebase",
                description=(
                    "Search the codebase for relevant code by keyword or concept. "
                    "Returns file path and snippet for each match."
                ),
            ),
            StructuredTool.from_function(
                func=list_directory,
                name="list_directory",
                description=(
                    "List the directory tree at a path relative to the repo root. "
                    "Use depth=1 for a shallow listing, depth=2 (default) for one level deeper. "
                    "Also usable as 'print_tree'."
                ),
            ),
            StructuredTool.from_function(
                func=read_file,
                name="read_file",
                description="Read a file inside the repository by path relative to its root.",
            ),
            StructuredTool.from_function(
                func=grep_code,
                name="grep_code",
                description=(
                    "Search the repo for an exact string or regex pattern using grep. "
                    "Returns path:line:match hits (up to 60). "
                    "Use for finding specific identifiers, imports, functions, or classes. "
                    "More precise than search_codebase for known exact strings."
                ),
            ),
        ]


_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv", ".git"}
)
_TEXT_EXTS: frozenset[str] = frozenset(
    {".py", ".ts", ".js", ".go", ".rs", ".md", ".yaml", ".toml", ".json", ".txt"}
)


def _grep_search(root: Path, query: str, max_results: int = 15) -> str:
    """Simple case-insensitive substring search as a Chroma fallback."""
    terms = [t.lower() for t in query.split() if len(t) > 2]
    if not terms:
        return "(no search terms)"
    hits: list[str] = []
    for p in root.rglob("*"):
        if len(hits) >= max_results:
            break
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix not in _TEXT_EXTS:
            continue
        try:
            text = p.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        lower = text.lower()
        if all(t in lower for t in terms):
            # Return a short snippet around the first match
            idx = lower.find(terms[0])
            snippet = text[max(0, idx - 60): idx + 200].replace("\n", " ")
            rel = p.relative_to(root)
            hits.append(f"{rel}: …{snippet}…")
    return "\n".join(hits) if hits else "(no matches found)"
