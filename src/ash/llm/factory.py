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
        if not api_key:
            raise RuntimeError(
                "No LLM credentials: set ANTHROPIC_API_KEY in the environment (or switch to an "
                "OpenAI-compatible gateway with LLM_PROVIDER=openai + LLM_BASE_URL)."
            )
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
        if not api_key and not llm.base_url:
            raise RuntimeError(
                "No LLM credentials: set OPENAI_API_KEY, or set LLM_BASE_URL to an "
                "OpenAI-compatible endpoint (LiteLLM/Ollama/vLLM)."
            )
        # The OpenAI SDK requires *some* api_key even when talking to a custom base_url; most
        # gateways ignore it. Send a placeholder when none is configured.
        return ChatOpenAI(
            model=llm.model,
            temperature=llm.temperature,
            api_key=SecretStr(api_key or "sk-noop"),
            base_url=llm.base_url,
            max_tokens=llm.max_tokens,  # type: ignore[call-arg]
        )
    raise ValueError(f"Unknown LLM provider: {llm.provider!r} (use 'anthropic' or 'openai')")
