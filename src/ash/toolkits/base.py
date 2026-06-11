"""Toolkit protocol: anything exposing a list of LangChain tools."""

from __future__ import annotations

from typing import Protocol

from langchain_core.tools import BaseTool


class Toolkit(Protocol):
    def get_tools(self) -> list[BaseTool]: ...
