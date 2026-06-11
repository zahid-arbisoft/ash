"""Codebase toolkit — read-only repo intelligence (search/read) as LangChain tools."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from ash.clients import code_intel


class CodebaseToolkit:
    """Sandboxed, read-only search/read over a checked-out worktree."""

    def __init__(self, *, root: Path) -> None:
        self._root = root

    def get_tools(self) -> list[BaseTool]:
        def search_code(query: str) -> str:
            """Search the repo for a string; returns 'path:line:match' hits."""
            return "\n".join(code_intel.search(self._root, query)) or "(no hits)"

        def read_file(path: str) -> str:
            """Read a file inside the repo by path relative to its root."""
            return code_intel.read_file(self._root, path) or "(file does not exist)"

        return [
            StructuredTool.from_function(
                func=search_code,
                name="search_code",
                description="Search the repository for a string; returns path:line:match hits.",
            ),
            StructuredTool.from_function(
                func=read_file,
                name="read_file",
                description="Read a file inside the repository by path relative to its root.",
            ),
        ]
