"""Provider-agnostic chat-model factory.

Returns a LangChain `BaseChatModel` for either Anthropic or any OpenAI-compatible endpoint
(LiteLLM / Ollama / vLLM / local gateway via `base_url`). Agents call `.with_structured_output(...)`
on the returned model to force a validated Pydantic shape, so no hand-rolled tool-call parsing.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from ash.config.settings import LLMSettings


def get_chat_model(llm: LLMSettings, *, api_key: str) -> BaseChatModel:
    if llm.provider == "anthropic":
        return ChatAnthropic(
            model_name=llm.model,
            temperature=llm.temperature,
            max_tokens_to_sample=llm.max_tokens,
            api_key=SecretStr(api_key),
            base_url=llm.base_url,
            timeout=None,
            stop=None,
        )
    if llm.provider == "openai":
        return ChatOpenAI(
            model=llm.model,
            temperature=llm.temperature,
            api_key=SecretStr(api_key) if api_key else None,
            base_url=llm.base_url,
            model_kwargs={"max_tokens": llm.max_tokens},
        )
    raise ValueError(f"Unknown LLM provider: {llm.provider!r} (use 'anthropic' or 'openai')")
