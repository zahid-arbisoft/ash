"""Toolkit layer: `@tool`/`StructuredTool` wrappers over boundary clients.

The middle of the 3-layer tool design (plan §6 of the boilerplate spec): `clients/` hold the real
logic, `toolkits/` expose LangChain `BaseTool`s with model-facing names/descriptions, and agents
bind only the toolkits they need. Today's agents call clients directly via structured output; the
toolkits are the bind-tools seam for future tool-calling agents (e.g. the deferred post-comment).
"""

from ash.toolkits.base import Toolkit
from ash.toolkits.board import BoardToolkit
from ash.toolkits.codebase import CodebaseToolkit

__all__ = ["BoardToolkit", "CodebaseToolkit", "Toolkit"]
