from ash.config.settings import Settings


def _settings() -> Settings:
    # ignore any developer .env so the tests are hermetic (env vars are still read)
    return Settings(_env_file=None)


def test_global_default_and_fallback(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    s = _settings()
    assert s.llm_provider == "anthropic"
    assert s.model_for("pm").model == s.llm_model
    assert s.api_key_for("anthropic") == "key"


def test_flat_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    s = _settings()
    assert s.model_for("pm").provider == "openai"
    assert s.model_for("pm").model == "gpt-4o"


def test_per_agent_override(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("AGENT_REVIEWER__MODEL", "claude-haiku-4-5")
    s = _settings()
    assert s.model_for("reviewer").model == "claude-haiku-4-5"
    assert s.model_for("pm").model == s.llm_model


def test_base_url_propagates(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:4000/v1")
    s = _settings()
    assert s.model_for("coding").base_url == "http://localhost:4000/v1"
