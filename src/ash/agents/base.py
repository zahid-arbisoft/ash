"""BaseAgent — the agent contract (boilerplate spec §4), running on LangChain `create_agent`.

An agent reads the root `WorkflowState`, does its work, and returns a partial update scoped to its
own namespace. Structured generation goes through `create_agent` (LangChain's maintained agent
runtime: the ReAct tool loop + `response_format` structured output + the middleware hook), so all
agents share one runtime instead of hand-rolled tool loops. The model and tools are injectable for
deterministic, offline tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar, cast

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from ash.config.settings import Settings
from ash.graph.state import WorkflowState
from ash.llm.factory import get_chat_model

T = TypeVar("T", bound=BaseModel)


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, settings: Settings, *, model: BaseChatModel | None = None) -> None:
        self.settings = settings
        self._model = model

    @abstractmethod
    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Return a partial state update scoped to this agent's namespace."""

    def get_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model
        llm = self.settings.model_for(self.name)
        return get_chat_model(llm, api_key=self.settings.api_key_for(llm.provider))

    def get_tools(self) -> list[BaseTool]:
        """Tools this agent may call inside its `create_agent` loop (default: none)."""
        return []

    def build_agent(
        self,
        *,
        system_prompt: str,
        response_format: type[BaseModel] | None = None,
        tools: list[BaseTool] | None = None,
    ) -> Any:
        """Construct this agent's `create_agent` runtime (its own compiled LangGraph)."""
        return create_agent(
            model=self.get_model(),
            tools=tools if tools is not None else self.get_tools(),
            system_prompt=system_prompt,
            response_format=response_format,
        )

    async def generate(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        tools: list[BaseTool] | None = None,
    ) -> T:
        """Run a `create_agent` with `response_format=schema` and return the validated object."""
        agent = self.build_agent(system_prompt=system, response_format=schema, tools=tools)
        result = await agent.ainvoke({"messages": [("user", user)]})
        return cast(T, result["structured_response"])
