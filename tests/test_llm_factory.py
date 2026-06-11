import pytest

from ash.config.settings import LLMSettings
from ash.llm.factory import get_chat_model


def test_anthropic_provider(monkeypatch):
    captured = {}

    class FakeChatAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("ash.llm.factory.ChatAnthropic", FakeChatAnthropic)
    get_chat_model(LLMSettings(provider="anthropic", model="claude-sonnet-4-6"), api_key="k")
    assert captured["model_name"] == "claude-sonnet-4-6"


def test_openai_provider(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("ash.llm.factory.ChatOpenAI", FakeChatOpenAI)
    get_chat_model(
        LLMSettings(provider="openai", model="gpt-4o", base_url="http://x/v1"), api_key="k"
    )
    assert captured["model"] == "gpt-4o"
    assert captured["base_url"] == "http://x/v1"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_chat_model(LLMSettings(provider="nope", model="x"), api_key="k")
