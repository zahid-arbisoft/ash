"""BaseAgent — the agent contract (boilerplate spec §4).

An agent reads the root `WorkflowState`, does its work, and returns a partial update scoped to its
own namespace. The LLM is provided by the provider-agnostic factory (per-agent model + global
fallback) and can be injected for deterministic, offline tests.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, TypeVar, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from ash.config.settings import Settings
from ash.graph.state import WorkflowState
from ash.llm.factory import get_chat_model

logger = logging.getLogger(__name__)

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

    async def generate(self, schema: type[T], *, system: str, user: str) -> T:
        """One-shot structured generation: force the model to return `schema`."""
        logger.debug("llm_call agent=%s schema=%s", self.name, schema.__name__)
        structured = self.get_model().with_structured_output(schema)
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        logger.debug("llm_done agent=%s schema=%s", self.name, schema.__name__)
        return cast(T, result)
