"""Codebase toolkit — read-only repo intelligence (search/read) as LangChain tools."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from ash.clients import code_intel
from ash.clients.chroma import VectorStoreClient


class CodebaseToolkit:
    """Sandboxed, read-only search/read over a checked-out worktree."""

    def __init__(self, *, client: VectorStoreClient, root: Path) -> None:
        self._client = client
        self._root = root

    def get_tools(self) -> list[BaseTool]:
        def search_codebase(query: str) -> str:
            """Semantically search the codebase. Returns relevant file path and snippet."""
            return "\n".join(self._client.search(query)) or "(no results)"

        def read_file(path: str) -> str:
            """Read a file inside the repo by path relative to its root."""
            return code_intel.read_file(self._root, path) or "(file does not exist)"

        return [
            StructuredTool.from_function(
                func=search_codebase,
                name="search_codebase",
                description=(
                    "Semantically search the codebase for relevant code. "
                    "Returns file path and snippet for each match."
                ),
            ),
            StructuredTool.from_function(
                func=read_file,
                name="read_file",
                description="Read a file inside the repository by path relative to its root.",
            ),
        ]
