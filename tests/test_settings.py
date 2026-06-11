from ash.config.settings import Settings


def test_global_default_and_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    s = Settings()
    assert s.llm.provider == "anthropic"
    assert s.model_for("pm").model == s.llm.model
    assert s.api_key_for("anthropic") == "key"


def test_per_agent_override(monkeypatch):
    monkeypatch.setenv("AGENT_REVIEWER__MODEL", "claude-haiku-4-5")
    s = Settings()
    assert s.model_for("reviewer").model == "claude-haiku-4-5"
    assert s.model_for("pm").model == s.llm.model


def test_base_url_propagates(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:4000/v1")
    s = Settings()
    assert s.model_for("coding").base_url == "http://localhost:4000/v1"
