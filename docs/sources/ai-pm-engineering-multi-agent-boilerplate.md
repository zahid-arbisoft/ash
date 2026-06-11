# AI-Driven PM + Engineering Multi-Agent Boilerplate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a structural skeleton plus one real vertical slice of an AI-driven PM+Engineering multi-agent system, where the full PM→Dev→Reviewer→Fixer→Merge graph runs end-to-end but only the PM agent does real work (reads a GitHub Issue, generates a spec, posts it back as a comment).

**Architecture:** Python `src/` package `agentsys` orchestrated with LangGraph (Postgres checkpointer). Agents are `BaseAgent` subclasses registered as graph nodes operating on one root `WorkflowState` with namespaced per-agent sub-states. Tools are three-layered: plain `clients/` → `@tool` `toolkits/` → agents bind toolkits. A FastAPI app starts runs as background tasks (returns `run_id`, status via checkpointer); an APScheduler job triggers the same internal runner. Chroma + Postgres run in docker-compose; S3/Redis are stubbed.

**Tech Stack:** Python 3.11+, uv, LangChain, LangGraph, langchain-anthropic, FastAPI, Pydantic / pydantic-settings, Postgres (langgraph checkpointer), Chroma, APScheduler, structlog, Langfuse, Ruff, mypy (strict), pytest + pytest-asyncio.

---

## File Structure

See spec §3 for the full tree. Files are grouped by responsibility:

- `config/settings.py` — all settings + per-agent model config
- `observability/logging.py`, `observability/langfuse.py` — logging + tracing
- `llm/factory.py` — provider-agnostic chat model factory
- `clients/{github,chroma,postgres,object_storage,redis}.py` — boundary clients (github/chroma/postgres real; rest stub)
- `toolkits/{base,board,git,pr,codebase,shell,messaging}.py` — `@tool` wrappers (board real; rest stub)
- `agents/{base,pm,dev,reviewer,fixer}.py` — agents (pm real; rest stub)
- `graph/{state,nodes,checkpointer,builder,runner}.py` — orchestration
- `api/{app,routes,schemas}.py` — FastAPI surface
- `scheduler/board_scan.py` — APScheduler job
- `tests/...` — mirrors the package

---

## Conventions for every task

- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.
- Run tests with `uv run pytest`. Run lint/type with `uv run ruff check .` and `uv run mypy src`.
- All agent/graph/client code is `async`. Tests use `pytest-asyncio` (`asyncio_mode = "auto"` set in Task 1).
- The LLM and external clients are **mocked** in tests — never hit a real API.
- Commit after each task with the shown message.

> **Version note for the implementer:** pin exact versions during Task 1. The imports below reflect current idiomatic APIs: `langchain_anthropic.ChatAnthropic`, `langchain_openai.ChatOpenAI`, `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`, `langgraph.graph.StateGraph`, `langfuse.langchain.CallbackHandler`, `chromadb.HttpClient`. If a pinned version exposes a different path, adjust the import and keep the behavior. Use context7 to confirm any uncertain API.

---

## Task 1: Project scaffolding (uv, pyproject, tooling, CI)

**Files:**
- Create: `pyproject.toml`
- Create: `.pre-commit-config.yaml`
- Create: `.github/workflows/ci.yml`
- Create: `.gitignore`
- Create: `src/agentsys/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

`tests/test_smoke.py`:
```python
def test_package_imports():
    import agentsys

    assert agentsys.__version__
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "agentsys"
version = "0.1.0"
description = "AI-driven PM + Engineering multi-agent boilerplate"
requires-python = ">=3.11"
dependencies = [
    "langchain>=0.3",
    "langchain-core>=0.3",
    "langchain-anthropic>=0.3",
    "langchain-openai>=0.2",
    "langgraph>=0.2",
    "langgraph-checkpoint-postgres>=2.0",
    "psycopg[binary,pool]>=3.2",
    "chromadb>=0.5",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "apscheduler>=3.10",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "structlog>=24.4",
    "langfuse>=2.50",
    "httpx>=0.27",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.7",
    "mypy>=1.13",
    "pre-commit>=4.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/agentsys"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.mypy]
python_version = "3.11"
strict = true
mypy_path = "src"
packages = ["agentsys"]
```

- [ ] **Step 3: Create `src/agentsys/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
.mypy_cache/
.pytest_cache/
.ruff_cache/
chroma-data/
postgres-data/
```

- [ ] **Step 5: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]
      - id: mypy
        name: mypy
        entry: uv run mypy src
        language: system
        pass_filenames: false
      - id: pytest-quick
        name: pytest quick
        entry: uv run pytest -q
        language: system
        pass_filenames: false
```

- [ ] **Step 6: Create `.github/workflows/ci.yml`**

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install 3.11
      - run: uv sync --all-extras --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest -q
```

- [ ] **Step 7: Create empty `tests/__init__.py`** (empty file)

- [ ] **Step 8: Sync and run the test**

Run: `uv sync --all-extras --dev && uv run pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore: scaffold project with uv, ruff, mypy, pytest, CI"
```

---

## Task 2: Settings (config layer)

**Files:**
- Create: `src/agentsys/config/__init__.py`
- Create: `src/agentsys/config/settings.py`
- Create: `.env.example`
- Test: `tests/config/test_settings.py`

- [ ] **Step 1: Write the failing test**

`tests/config/test_settings.py`:
```python
from agentsys.config.settings import Settings


def test_defaults_and_global_fallback(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    s = Settings()

    assert s.llm.provider == "anthropic"
    assert s.llm.model == "claude-sonnet-4-6"
    # per-agent override falls back to global default when unset
    assert s.model_for("pm").model == "claude-sonnet-4-6"


def test_per_agent_override(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("AGENT_REVIEWER__MODEL", "claude-haiku-4-5")
    s = Settings()

    assert s.model_for("reviewer").model == "claude-haiku-4-5"
    assert s.model_for("pm").model == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_settings.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create `src/agentsys/config/__init__.py`** (empty file)

- [ ] **Step 4: Implement `src/agentsys/config/settings.py`**

```python
from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentName = str  # "pm" | "dev" | "reviewer" | "fixer"


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0


class AgentModelOverride(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # secrets / connections
    github_token: str = ""
    github_repo: str = "owner/repo"  # "owner/name" the agents operate on
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    postgres_dsn: str = "postgresql://agentsys:agentsys@localhost:5432/agentsys"
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # llm
    llm: LLMSettings = LLMSettings()
    agent_pm: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_dev: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_reviewer: AgentModelOverride = Field(default_factory=AgentModelOverride)
    agent_fixer: AgentModelOverride = Field(default_factory=AgentModelOverride)

    # scheduler
    board_scan_interval_seconds: int = 300
    board_scan_enabled: bool = False

    # langfuse (env-gated)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # logging
    log_level: str = "INFO"

    def model_for(self, agent: AgentName) -> LLMSettings:
        override: AgentModelOverride = getattr(self, f"agent_{agent}")
        return LLMSettings(
            provider=override.provider or self.llm.provider,
            model=override.model or self.llm.model,
            temperature=(
                override.temperature
                if override.temperature is not None
                else self.llm.temperature
            ),
        )
```

- [ ] **Step 5: Create `.env.example`**

```
# --- Secrets / connections ---
GITHUB_TOKEN=
GITHUB_REPO=owner/repo
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
POSTGRES_DSN=postgresql://agentsys:agentsys@localhost:5432/agentsys
CHROMA_HOST=localhost
CHROMA_PORT=8000

# --- LLM (global default) ---
LLM__PROVIDER=anthropic
LLM__MODEL=claude-sonnet-4-6
LLM__TEMPERATURE=0.0

# --- Per-agent overrides (optional; blank = use global default) ---
# AGENT_REVIEWER__MODEL=claude-haiku-4-5
# AGENT_DEV__MODEL=claude-opus-4-8

# --- Scheduler ---
BOARD_SCAN_ENABLED=false
BOARD_SCAN_INTERVAL_SECONDS=300

# --- Langfuse (leave blank to disable tracing) ---
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# --- Logging ---
LOG_LEVEL=INFO
```

- [ ] **Step 6: Create `tests/config/__init__.py`** (empty file)

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/config/test_settings.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(config): add settings with per-agent model overrides"
```

---

## Task 3: Observability — structlog logging

**Files:**
- Create: `src/agentsys/observability/__init__.py`
- Create: `src/agentsys/observability/logging.py`
- Test: `tests/observability/test_logging.py`

- [ ] **Step 1: Write the failing test**

`tests/observability/test_logging.py`:
```python
import structlog

from agentsys.observability.logging import bind_run_context, configure_logging


def test_configure_and_bind_run_context():
    configure_logging("INFO")
    bind_run_context(run_id="r1", thread_id="t1")
    logger = structlog.get_logger()
    bound = logger.bind()
    # contextvars carry run_id/thread_id into every event
    assert structlog.contextvars.get_contextvars()["run_id"] == "r1"
    assert structlog.contextvars.get_contextvars()["thread_id"] == "t1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/observability/test_logging.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/observability/logging.py`**

```python
from __future__ import annotations

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper()))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper())
        ),
        cache_logger_on_first_use=True,
    )


def bind_run_context(*, run_id: str, thread_id: str) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id, thread_id=thread_id)
```

- [ ] **Step 4: Create `src/agentsys/observability/__init__.py`** (empty) and `tests/observability/__init__.py` (empty)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/observability/test_logging.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(observability): structlog JSON logging with run context binding"
```

---

## Task 4: Observability — Langfuse callback factory (env-gated)

**Files:**
- Create: `src/agentsys/observability/langfuse.py`
- Test: `tests/observability/test_langfuse.py`

- [ ] **Step 1: Write the failing test**

`tests/observability/test_langfuse.py`:
```python
from agentsys.config.settings import Settings
from agentsys.observability.langfuse import make_langfuse_handler


def test_returns_none_when_unset():
    s = Settings(langfuse_public_key="", langfuse_secret_key="")
    assert make_langfuse_handler(s) is None


def test_returns_handler_when_keys_present(monkeypatch):
    created = {}

    class FakeHandler:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(
        "agentsys.observability.langfuse._CallbackHandler", FakeHandler
    )
    s = Settings(
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_host="https://lf.example",
    )
    handler = make_langfuse_handler(s)
    assert handler is not None
    assert created["public_key"] == "pk"
    assert created["host"] == "https://lf.example"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/observability/test_langfuse.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/observability/langfuse.py`**

```python
from __future__ import annotations

from typing import Any

from agentsys.config.settings import Settings

try:  # langfuse is optional at runtime
    from langfuse.langchain import CallbackHandler as _CallbackHandler
except Exception:  # pragma: no cover - import guard
    _CallbackHandler = None  # type: ignore[assignment, misc]


def make_langfuse_handler(settings: Settings) -> Any | None:
    """Return a Langfuse LangChain callback handler, or None if disabled."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    if _CallbackHandler is None:  # pragma: no cover
        return None
    return _CallbackHandler(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
```

> **Version note:** if the pinned `langfuse` exposes the handler at
> `langfuse.callback.CallbackHandler` instead, update the import. The factory
> contract (None when unset, handler when keys present) stays identical.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/observability/test_langfuse.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(observability): env-gated Langfuse callback factory"
```

---

## Task 5: LLM factory (provider-agnostic)

**Files:**
- Create: `src/agentsys/llm/__init__.py`
- Create: `src/agentsys/llm/factory.py`
- Test: `tests/llm/test_factory.py`

- [ ] **Step 1: Write the failing test**

`tests/llm/test_factory.py`:
```python
import pytest

from agentsys.config.settings import LLMSettings
from agentsys.llm.factory import get_chat_model


def test_anthropic_provider(monkeypatch):
    captured = {}

    class FakeChatAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("agentsys.llm.factory.ChatAnthropic", FakeChatAnthropic)
    get_chat_model(LLMSettings(provider="anthropic", model="claude-sonnet-4-6"), api_key="k")
    assert captured["model"] == "claude-sonnet-4-6"


def test_openai_provider(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("agentsys.llm.factory.ChatOpenAI", FakeChatOpenAI)
    get_chat_model(LLMSettings(provider="openai", model="gpt-4o"), api_key="k")
    assert captured["model"] == "gpt-4o"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_chat_model(LLMSettings(provider="nope", model="x"), api_key="k")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_factory.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/llm/factory.py`**

```python
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agentsys.config.settings import LLMSettings


def get_chat_model(llm: LLMSettings, *, api_key: str) -> BaseChatModel:
    if llm.provider == "anthropic":
        return ChatAnthropic(
            model=llm.model, temperature=llm.temperature, api_key=api_key
        )
    if llm.provider == "openai":
        return ChatOpenAI(
            model=llm.model, temperature=llm.temperature, api_key=api_key
        )
    raise ValueError(f"Unknown LLM provider: {llm.provider}")
```

- [ ] **Step 4: Create `src/agentsys/llm/__init__.py`** (empty) and `tests/llm/__init__.py` (empty)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_factory.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(llm): provider-agnostic chat model factory"
```

---

## Task 6: GitHub client (real) + stub clients

**Files:**
- Create: `src/agentsys/clients/__init__.py`
- Create: `src/agentsys/clients/github.py`
- Create: `src/agentsys/clients/object_storage.py`
- Create: `src/agentsys/clients/redis.py`
- Test: `tests/clients/test_github.py`
- Test: `tests/clients/test_stubs.py`

- [ ] **Step 1: Write the failing test**

`tests/clients/test_github.py`:
```python
import httpx
import pytest

from agentsys.clients.github import GitHubClient, Issue


@pytest.mark.asyncio
async def test_get_issue_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(
            200, json={"number": 42, "title": "Bug", "body": "It broke"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(token="tok", repo="o/r", http=http)
        issue = await client.get_issue("42")

    assert issue == Issue(number=42, title="Bug", body="It broke")


@pytest.mark.asyncio
async def test_post_comment_returns_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"html_url": "https://gh/comment/1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(token="tok", repo="o/r", http=http)
        url = await client.post_comment("42", "the spec")

    assert url == "https://gh/comment/1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/clients/test_github.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/clients/github.py`**

```python
from __future__ import annotations

import httpx
from pydantic import BaseModel


class Issue(BaseModel):
    number: int
    title: str
    body: str


class GitHubClient:
    """Minimal GitHub Issues client: read an issue, post a comment."""

    def __init__(self, *, token: str, repo: str, http: httpx.AsyncClient) -> None:
        self._token = token
        self._repo = repo  # "owner/name"
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "accept": "application/vnd.github+json",
        }

    async def get_issue(self, item_id: str) -> Issue:
        resp = await self._http.get(
            f"/repos/{self._repo}/issues/{item_id}", headers=self._headers
        )
        resp.raise_for_status()
        data = resp.json()
        return Issue(number=data["number"], title=data["title"], body=data.get("body") or "")

    async def post_comment(self, item_id: str, body: str) -> str:
        resp = await self._http.post(
            f"/repos/{self._repo}/issues/{item_id}/comments",
            headers=self._headers,
            json={"body": body},
        )
        resp.raise_for_status()
        return str(resp.json()["html_url"])
```

- [ ] **Step 4: Write the stub-client failing test**

`tests/clients/test_stubs.py`:
```python
import pytest

from agentsys.clients.object_storage import ObjectStorageClient
from agentsys.clients.redis import RedisClient


@pytest.mark.asyncio
async def test_object_storage_stub_raises():
    with pytest.raises(NotImplementedError):
        await ObjectStorageClient().put("k", b"v")


@pytest.mark.asyncio
async def test_redis_stub_raises():
    with pytest.raises(NotImplementedError):
        await RedisClient().get("k")
```

- [ ] **Step 5: Implement the stub clients**

`src/agentsys/clients/object_storage.py`:
```python
from __future__ import annotations


class ObjectStorageClient:
    """STUB. TODO: implement S3/GCS object storage."""

    async def put(self, key: str, data: bytes) -> str:
        raise NotImplementedError("ObjectStorageClient.put is not implemented yet")

    async def get(self, key: str) -> bytes:
        raise NotImplementedError("ObjectStorageClient.get is not implemented yet")
```

`src/agentsys/clients/redis.py`:
```python
from __future__ import annotations


class RedisClient:
    """STUB. TODO: implement Redis cache/queue."""

    async def get(self, key: str) -> str | None:
        raise NotImplementedError("RedisClient.get is not implemented yet")

    async def set(self, key: str, value: str) -> None:
        raise NotImplementedError("RedisClient.set is not implemented yet")
```

- [ ] **Step 6: Create `src/agentsys/clients/__init__.py`** (empty) and `tests/clients/__init__.py` (empty)

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/clients/ -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(clients): real GitHub Issues client + stub object-storage/redis"
```

---

## Task 7: Chroma + Postgres clients (real)

**Files:**
- Create: `src/agentsys/clients/chroma.py`
- Create: `src/agentsys/clients/postgres.py`
- Test: `tests/clients/test_chroma.py`

- [ ] **Step 1: Write the failing test**

`tests/clients/test_chroma.py`:
```python
from agentsys.clients.chroma import VectorStoreClient


def test_get_or_create_collection_delegates(monkeypatch):
    calls = {}

    class FakeCollection:
        def add(self, **kwargs):
            calls["add"] = kwargs

    class FakeChroma:
        def get_or_create_collection(self, name):
            calls["collection"] = name
            return FakeCollection()

    client = VectorStoreClient(chroma=FakeChroma())
    client.add("specs", ids=["1"], documents=["hello"])
    assert calls["collection"] == "specs"
    assert calls["add"]["ids"] == ["1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/clients/test_chroma.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/clients/chroma.py`**

```python
from __future__ import annotations

from typing import Any, Protocol


class _ChromaLike(Protocol):
    def get_or_create_collection(self, name: str) -> Any: ...


class VectorStoreClient:
    """Thin wrapper over a Chroma client (real, but unused by the PM slice)."""

    def __init__(self, *, chroma: _ChromaLike) -> None:
        self._chroma = chroma

    def add(self, collection: str, *, ids: list[str], documents: list[str]) -> None:
        col = self._chroma.get_or_create_collection(collection)
        col.add(ids=ids, documents=documents)

    @classmethod
    def from_settings(cls, host: str, port: int) -> VectorStoreClient:
        import chromadb

        return cls(chroma=chromadb.HttpClient(host=host, port=port))
```

- [ ] **Step 4: Implement `src/agentsys/clients/postgres.py`**

```python
from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


def make_pool(dsn: str) -> AsyncConnectionPool:
    """Create (but do not open) an async Postgres connection pool."""
    return AsyncConnectionPool(conninfo=dsn, open=False)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/clients/test_chroma.py -v`
Expected: PASS

> Note: `postgres.py` `make_pool` is covered indirectly via the checkpointer
> (Task 11) and API lifespan; no isolated unit test needed since it is a one-line
> delegation to the driver.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(clients): Chroma vector-store wrapper + Postgres pool factory"
```

---

## Task 8: Toolkit base + Board toolkit (real)

**Files:**
- Create: `src/agentsys/toolkits/__init__.py`
- Create: `src/agentsys/toolkits/base.py`
- Create: `src/agentsys/toolkits/board.py`
- Test: `tests/toolkits/test_board.py`

- [ ] **Step 1: Write the failing test**

`tests/toolkits/test_board.py`:
```python
import pytest
from langchain_core.tools import BaseTool

from agentsys.clients.github import Issue
from agentsys.toolkits.board import BoardToolkit


class FakeGitHub:
    def __init__(self):
        self.posted = None

    async def get_issue(self, item_id):
        return Issue(number=int(item_id), title="T", body="B")

    async def post_comment(self, item_id, body):
        self.posted = (item_id, body)
        return "https://gh/comment/9"


def test_get_tools_returns_base_tools():
    tk = BoardToolkit(github=FakeGitHub())
    tools = tk.get_tools()
    names = {t.name for t in tools}
    assert names == {"read_board_item", "post_board_comment"}
    assert all(isinstance(t, BaseTool) for t in tools)
    # descriptions are non-empty (the model relies on them)
    assert all(t.description for t in tools)


@pytest.mark.asyncio
async def test_tools_invoke_client():
    gh = FakeGitHub()
    tk = BoardToolkit(github=gh)
    tools = {t.name: t for t in tk.get_tools()}

    read = await tools["read_board_item"].ainvoke({"item_id": "42"})
    assert "T" in read and "B" in read

    url = await tools["post_board_comment"].ainvoke(
        {"item_id": "42", "body": "the spec"}
    )
    assert url == "https://gh/comment/9"
    assert gh.posted == ("42", "the spec")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/toolkits/test_board.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/toolkits/base.py`**

```python
from __future__ import annotations

from typing import Protocol

from langchain_core.tools import BaseTool


class Toolkit(Protocol):
    def get_tools(self) -> list[BaseTool]: ...
```

- [ ] **Step 4: Implement `src/agentsys/toolkits/board.py`**

```python
from __future__ import annotations

from typing import Protocol

from langchain_core.tools import BaseTool, StructuredTool

from agentsys.clients.github import Issue


class _BoardClient(Protocol):
    async def get_issue(self, item_id: str) -> Issue: ...
    async def post_comment(self, item_id: str, body: str) -> str: ...


class BoardToolkit:
    """Real toolkit wrapping a board client (GitHub Issues)."""

    def __init__(self, *, github: _BoardClient) -> None:
        self._github = github

    def get_tools(self) -> list[BaseTool]:
        async def read_board_item(item_id: str) -> str:
            """Read a board item (GitHub issue) by its id. Returns title and body."""
            issue = await self._github.get_issue(item_id)
            return f"Title: {issue.title}\n\nBody:\n{issue.body}"

        async def post_board_comment(item_id: str, body: str) -> str:
            """Post a comment on a board item. Returns the comment URL."""
            return await self._github.post_comment(item_id, body)

        return [
            StructuredTool.from_function(
                coroutine=read_board_item,
                name="read_board_item",
                description="Read a board item (GitHub issue) by id; returns title and body.",
            ),
            StructuredTool.from_function(
                coroutine=post_board_comment,
                name="post_board_comment",
                description="Post a comment on a board item by id; returns the comment URL.",
            ),
        ]
```

- [ ] **Step 5: Create `src/agentsys/toolkits/__init__.py`** (empty) and `tests/toolkits/__init__.py` (empty)

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/toolkits/test_board.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(toolkits): toolkit protocol + real board toolkit over GitHub client"
```

---

## Task 9: Stub toolkits

**Files:**
- Create: `src/agentsys/toolkits/git.py`
- Create: `src/agentsys/toolkits/pr.py`
- Create: `src/agentsys/toolkits/codebase.py`
- Create: `src/agentsys/toolkits/shell.py`
- Create: `src/agentsys/toolkits/messaging.py`
- Test: `tests/toolkits/test_stubs.py`

- [ ] **Step 1: Write the failing test**

`tests/toolkits/test_stubs.py`:
```python
import pytest

from agentsys.toolkits.codebase import CodebaseToolkit
from agentsys.toolkits.git import GitToolkit
from agentsys.toolkits.messaging import MessagingToolkit
from agentsys.toolkits.pr import PRToolkit
from agentsys.toolkits.shell import ShellToolkit


@pytest.mark.parametrize(
    "toolkit_cls",
    [GitToolkit, PRToolkit, CodebaseToolkit, ShellToolkit, MessagingToolkit],
)
def test_stub_toolkits_not_implemented(toolkit_cls):
    with pytest.raises(NotImplementedError):
        toolkit_cls().get_tools()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/toolkits/test_stubs.py -v`
Expected: FAIL (modules not found)

- [ ] **Step 3: Implement each stub toolkit**

Create each of the five files with the same shape (change the class name and the TODO text per file):

`src/agentsys/toolkits/git.py`:
```python
from __future__ import annotations

from langchain_core.tools import BaseTool


class GitToolkit:
    """STUB. TODO: implement git operations (branch, commit) for the Dev agent."""

    def get_tools(self) -> list[BaseTool]:
        raise NotImplementedError("GitToolkit.get_tools is not implemented yet")
```

`src/agentsys/toolkits/pr.py` — class `PRToolkit`, TODO "implement GitHub PR APIs (create/comment/merge PR)".
`src/agentsys/toolkits/codebase.py` — class `CodebaseToolkit`, TODO "implement semantic/regex codebase search".
`src/agentsys/toolkits/shell.py` — class `ShellToolkit`, TODO "implement sandboxed run-commands (lint/test)".
`src/agentsys/toolkits/messaging.py` — class `MessagingToolkit`, TODO "implement Slack/Teams messaging".

Each body is identical to `GitToolkit` except the class name and message.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/toolkits/test_stubs.py -v`
Expected: PASS (5 parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(toolkits): stub git/pr/codebase/shell/messaging toolkits"
```

---

## Task 10: Graph state (namespaced sub-states)

**Files:**
- Create: `src/agentsys/graph/__init__.py`
- Create: `src/agentsys/graph/state.py`
- Test: `tests/graph/test_state.py`

- [ ] **Step 1: Write the failing test**

`tests/graph/test_state.py`:
```python
from agentsys.graph.state import WorkflowState


def test_default_substates_present():
    state = WorkflowState(run_id="r1", board="github", item_id="42")
    assert state.pm.spec is None
    assert state.dev.note is None
    assert state.reviewer.note is None
    assert state.fixer.note is None
    assert state.status == "running"


def test_substates_are_isolated():
    state = WorkflowState(run_id="r1", board="github", item_id="42")
    state.pm.spec = "S"
    assert state.dev.note is None  # writing pm must not touch dev
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_state.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/graph/state.py`**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PMState(BaseModel):
    spec: str | None = None
    comment_url: str | None = None
    error: str | None = None


class DevState(BaseModel):
    note: str | None = None
    error: str | None = None


class ReviewerState(BaseModel):
    note: str | None = None
    error: str | None = None


class FixerState(BaseModel):
    note: str | None = None
    error: str | None = None


class WorkflowState(BaseModel):
    run_id: str
    board: str
    item_id: str
    pm: PMState = Field(default_factory=PMState)
    dev: DevState = Field(default_factory=DevState)
    reviewer: ReviewerState = Field(default_factory=ReviewerState)
    fixer: FixerState = Field(default_factory=FixerState)
    status: Literal["running", "completed", "failed"] = "running"
```

- [ ] **Step 4: Create `src/agentsys/graph/__init__.py`** (empty) and `tests/graph/__init__.py` (empty)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/graph/test_state.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(graph): WorkflowState with namespaced per-agent sub-states"
```

---

## Task 11: Postgres checkpointer factory

**Files:**
- Create: `src/agentsys/graph/checkpointer.py`
- Test: `tests/graph/test_checkpointer.py`

- [ ] **Step 1: Write the failing test**

`tests/graph/test_checkpointer.py`:
```python
from agentsys.graph.checkpointer import checkpointer_from_dsn


def test_factory_builds_context_manager(monkeypatch):
    captured = {}

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, dsn):
            captured["dsn"] = dsn
            return "CM"

    monkeypatch.setattr(
        "agentsys.graph.checkpointer.AsyncPostgresSaver", FakeSaver
    )
    cm = checkpointer_from_dsn("postgresql://x")
    assert cm == "CM"
    assert captured["dsn"] == "postgresql://x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_checkpointer.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/graph/checkpointer.py`**

```python
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def checkpointer_from_dsn(dsn: str) -> Any:
    """Return an async context manager yielding an AsyncPostgresSaver.

    Usage:
        async with checkpointer_from_dsn(dsn) as saver:
            await saver.setup()
            graph = build_graph(..., checkpointer=saver)
    """
    return AsyncPostgresSaver.from_conn_string(dsn)
```

> **Version note:** `AsyncPostgresSaver.from_conn_string` returns an async
> context manager. The first time against a fresh DB, call `await saver.setup()`
> to create checkpoint tables (done in API lifespan, Task 17).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/graph/test_checkpointer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(graph): async Postgres checkpointer factory"
```

---

## Task 12: BaseAgent + stub agents (dev/reviewer/fixer)

**Files:**
- Create: `src/agentsys/agents/__init__.py`
- Create: `src/agentsys/agents/base.py`
- Create: `src/agentsys/agents/dev.py`
- Create: `src/agentsys/agents/reviewer.py`
- Create: `src/agentsys/agents/fixer.py`
- Test: `tests/agents/test_stub_agents.py`

- [ ] **Step 1: Write the failing test**

`tests/agents/test_stub_agents.py`:
```python
import pytest

from agentsys.agents.dev import DevAgent
from agentsys.agents.fixer import FixerAgent
from agentsys.agents.reviewer import ReviewerAgent
from agentsys.config.settings import Settings
from agentsys.graph.state import WorkflowState


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_cls,key",
    [(DevAgent, "dev"), (ReviewerAgent, "reviewer"), (FixerAgent, "fixer")],
)
async def test_stub_agent_annotates_its_namespace(agent_cls, key):
    settings = Settings(github_token="t", anthropic_api_key="k")
    agent = agent_cls(settings)
    state = WorkflowState(run_id="r1", board="github", item_id="42")

    update = await agent.run(state)

    assert key in update
    assert update[key]["note"]  # a placeholder note was written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_stub_agents.py -v`
Expected: FAIL (modules not found)

- [ ] **Step 3: Implement `src/agentsys/agents/base.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import BaseTool

from agentsys.config.settings import Settings
from agentsys.graph.state import WorkflowState
from agentsys.llm.factory import get_chat_model


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Return a partial state update scoped to this agent's namespace."""

    def get_model(self) -> BaseChatModel:
        llm = self.settings.model_for(self.name)
        api_key = (
            self.settings.anthropic_api_key
            if llm.provider == "anthropic"
            else self.settings.openai_api_key
        )
        return get_chat_model(llm, api_key=api_key)

    def get_tools(self) -> list[BaseTool]:
        return []

    def get_prompt(self) -> ChatPromptTemplate:
        return ChatPromptTemplate.from_messages([("system", "You are an agent.")])
```

- [ ] **Step 4: Implement the three stub agents**

`src/agentsys/agents/dev.py`:
```python
from __future__ import annotations

from typing import Any

from agentsys.agents.base import BaseAgent
from agentsys.graph.state import WorkflowState


class DevAgent(BaseAgent):
    name = "dev"

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        # STUB. TODO: pick ticket, explore codebase, implement, open PR.
        return {"dev": {"note": "dev stub: not implemented"}}
```

`src/agentsys/agents/reviewer.py` — class `ReviewerAgent`, `name = "reviewer"`, returns `{"reviewer": {"note": "reviewer stub: not implemented"}}`.
`src/agentsys/agents/fixer.py` — class `FixerAgent`, `name = "fixer"`, returns `{"fixer": {"note": "fixer stub: not implemented"}}`.

- [ ] **Step 5: Create `src/agentsys/agents/__init__.py`** (empty) and `tests/agents/__init__.py` (empty)

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_stub_agents.py -v`
Expected: PASS (3 parametrized cases)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(agents): BaseAgent contract + dev/reviewer/fixer stubs"
```

---

## Task 13: PM agent (real)

**Files:**
- Create: `src/agentsys/agents/pm.py`
- Test: `tests/agents/test_pm.py`

The PM agent runs a real LangChain **tool-calling loop**: it binds the board toolkit's `read_board_item` tool to the model, lets the model invoke it to read the issue, then takes the model's final message as the spec and **posts it deterministically** as a comment (so the slice always produces output). This is the part of the boilerplate that proves the `bind_tools` path (spec §6) actually works end-to-end. To keep it deterministic and testable, the agent is constructed with its board client + model injected, and the model is mocked to emit a tool call then a final spec.

- [ ] **Step 1: Write the failing test**

`tests/agents/test_pm.py`:
```python
import pytest
from langchain_core.messages import AIMessage

from agentsys.agents.pm import PMAgent
from agentsys.clients.github import Issue
from agentsys.config.settings import Settings
from agentsys.graph.state import WorkflowState


class FakeGitHub:
    def __init__(self):
        self.posted = None

    async def get_issue(self, item_id):
        return Issue(number=int(item_id), title="Add export", body="Users want CSV export")

    async def post_comment(self, item_id, body):
        self.posted = (item_id, body)
        return "https://gh/comment/7"


class FakeModel:
    """Emits a read_board_item tool call on turn 1, then a final spec on turn 2."""

    def __init__(self) -> None:
        self.turn = 0
        self.bound_tool_names: list[str] = []

    def bind_tools(self, tools):
        self.bound_tool_names = [t.name for t in tools]
        return self

    async def ainvoke(self, messages, **kwargs):
        self.turn += 1
        if self.turn == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_board_item",
                        "args": {"item_id": "42"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="## Technical Spec\n- Add CSV export endpoint")


@pytest.mark.asyncio
async def test_pm_reads_via_tool_then_posts_spec():
    gh = FakeGitHub()
    model = FakeModel()
    settings = Settings(github_token="t", anthropic_api_key="k")
    agent = PMAgent(settings, github=gh, model=model)
    state = WorkflowState(run_id="r1", board="github", item_id="42")

    update = await agent.run(state)

    # the model was given the read tool and chose to call it
    assert model.bound_tool_names == ["read_board_item"]
    assert "CSV export" in update["pm"]["spec"]
    assert update["pm"]["comment_url"] == "https://gh/comment/7"
    # the generated spec is what got posted back to the board
    assert gh.posted is not None
    assert "CSV export" in gh.posted[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agents/test_pm.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/agents/pm.py`**

```python
from __future__ import annotations

from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from agentsys.agents.base import BaseAgent
from agentsys.clients.github import Issue
from agentsys.config.settings import Settings
from agentsys.graph.state import WorkflowState
from agentsys.toolkits.board import BoardToolkit

_SYSTEM = (
    "You are a senior product manager. Use the available tools to read the board "
    "item, then produce a concise, actionable technical specification: problem, "
    "proposed approach, acceptance criteria, and suggested tickets. Output Markdown. "
    "When finished, respond with only the specification and make no further tool calls."
)


class _BoardClient(Protocol):
    async def get_issue(self, item_id: str) -> Issue: ...
    async def post_comment(self, item_id: str, body: str) -> str: ...


class PMAgent(BaseAgent):
    name = "pm"

    def __init__(
        self,
        settings: Settings,
        *,
        github: _BoardClient,
        model: BaseChatModel | None = None,
        max_iterations: int = 5,
    ) -> None:
        super().__init__(settings)
        self._github = github
        self._model = model or self.get_model()
        self._max_iterations = max_iterations

    def get_tools(self) -> list[BaseTool]:
        # PM only needs to READ the board; posting is done deterministically in run()
        # so the slice always produces a comment. The post tool stays available in
        # BoardToolkit for future agents that should let the LLM decide to post.
        return [
            t
            for t in BoardToolkit(github=self._github).get_tools()
            if t.name == "read_board_item"
        ]

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        tools = {t.name: t for t in self.get_tools()}
        model = self._model.bind_tools(list(tools.values()))
        messages: list[Any] = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(
                content=f"Read board item {state.item_id} and write its technical spec."
            ),
        ]

        spec = ""
        for _ in range(self._max_iterations):
            response = await model.ainvoke(messages)
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                spec = (
                    response.content
                    if isinstance(response.content, str)
                    else str(response.content)
                )
                break
            for call in tool_calls:
                tool = tools[call["name"]]
                result = await tool.ainvoke(call["args"])
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=call["id"])
                )

        comment_url = await self._github.post_comment(state.item_id, spec)
        return {"pm": {"spec": spec, "comment_url": comment_url}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agents/test_pm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(agents): real PM agent — read issue, generate spec, post comment"
```

---

## Task 14: Graph nodes (adapters with error handling)

**Files:**
- Create: `src/agentsys/graph/nodes.py`
- Test: `tests/graph/test_nodes.py`

- [ ] **Step 1: Write the failing test**

`tests/graph/test_nodes.py`:
```python
import pytest

from agentsys.graph.nodes import make_node
from agentsys.graph.state import WorkflowState


class OkAgent:
    name = "dev"

    async def run(self, state):
        return {"dev": {"note": "done"}}


class BoomAgent:
    name = "dev"

    async def run(self, state):
        raise RuntimeError("kaboom")


@pytest.mark.asyncio
async def test_node_passes_update_through():
    node = make_node(OkAgent())
    state = WorkflowState(run_id="r", board="github", item_id="1")
    update = await node(state)
    assert update["dev"]["note"] == "done"


@pytest.mark.asyncio
async def test_node_captures_error_into_namespace():
    node = make_node(BoomAgent())
    state = WorkflowState(run_id="r", board="github", item_id="1")
    update = await node(state)
    assert "kaboom" in update["dev"]["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_nodes.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/graph/nodes.py`**

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import structlog

from agentsys.graph.state import WorkflowState

logger = structlog.get_logger()


class _Agent(Protocol):
    name: str

    async def run(self, state: WorkflowState) -> dict[str, Any]: ...


def make_node(agent: _Agent) -> Callable[[WorkflowState], Awaitable[dict[str, Any]]]:
    async def node(state: WorkflowState) -> dict[str, Any]:
        try:
            return await agent.run(state)
        except Exception as exc:  # noqa: BLE001 - we record, never crash the run
            logger.error("agent_failed", agent=agent.name, error=str(exc))
            return {agent.name: {"error": str(exc)}}

    return node
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/graph/test_nodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(graph): node adapter with per-agent error capture"
```

---

## Task 15: Graph builder + merge node

**Files:**
- Create: `src/agentsys/graph/builder.py`
- Test: `tests/graph/test_builder.py`

This wires PM→Dev→Reviewer→Fixer→Merge into a `StateGraph`. The PM agent needs its board client + model, so the builder takes a factory map of agents. The merge node sets terminal status.

- [ ] **Step 1: Write the failing test**

`tests/graph/test_builder.py`:
```python
import pytest
from langgraph.checkpoint.memory import MemorySaver

from agentsys.graph.builder import build_graph
from agentsys.graph.state import WorkflowState


class StubAgent:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        return {self.name: {"note": f"{self.name} ran"}}


@pytest.mark.asyncio
async def test_graph_traverses_all_nodes_and_completes():
    agents = {n: StubAgent(n) for n in ["pm", "dev", "reviewer", "fixer"]}
    graph = build_graph(agents, checkpointer=MemorySaver())

    initial = WorkflowState(run_id="r1", board="github", item_id="42")
    config = {"configurable": {"thread_id": "r1"}}
    result = await graph.ainvoke(initial, config=config)

    assert result["pm"]["note"] == "pm ran"
    assert result["fixer"]["note"] == "fixer ran"
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_graph_marks_failed_when_substate_has_error():
    agents = {n: StubAgent(n) for n in ["pm", "dev", "reviewer", "fixer"]}

    class FailingPM:
        name = "pm"

        async def run(self, state):
            return {"pm": {"error": "boom"}}

    agents["pm"] = FailingPM()
    graph = build_graph(agents, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "r2"}}
    result = await graph.ainvoke(
        WorkflowState(run_id="r2", board="github", item_id="1"), config=config
    )
    assert result["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_builder.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/graph/builder.py`**

```python
from __future__ import annotations

from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from agentsys.graph.nodes import make_node
from agentsys.graph.state import WorkflowState


class _Agent(Protocol):
    name: str

    async def run(self, state: WorkflowState) -> dict[str, Any]: ...


async def _merge(state: WorkflowState) -> dict[str, Any]:
    errored = any(
        sub.error is not None
        for sub in (state.pm, state.dev, state.reviewer, state.fixer)
    )
    return {"status": "failed" if errored else "completed"}


def build_graph(agents: dict[str, _Agent], *, checkpointer: Any) -> Any:
    graph: StateGraph = StateGraph(WorkflowState)

    graph.add_node("pm", make_node(agents["pm"]))
    graph.add_node("dev", make_node(agents["dev"]))
    graph.add_node("reviewer", make_node(agents["reviewer"]))
    graph.add_node("fixer", make_node(agents["fixer"]))
    graph.add_node("merge", _merge)

    graph.add_edge(START, "pm")
    graph.add_edge("pm", "dev")
    graph.add_edge("dev", "reviewer")
    graph.add_edge("reviewer", "fixer")
    graph.add_edge("fixer", "merge")
    graph.add_edge("merge", END)

    return graph.compile(checkpointer=checkpointer)
```

> **Version note:** LangGraph applies each node's returned dict as a state update.
> Because `WorkflowState` is a Pydantic model with nested models, returning
> `{"pm": {"note": ...}}` replaces the `pm` sub-state. That is the intended
> namespace-replacement semantics for this boilerplate. If the pinned LangGraph
> version requires `TypedDict` state, convert `WorkflowState` to a `TypedDict`
> with the same field names and nested models; the namespace invariant is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/graph/test_builder.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(graph): build PM->Dev->Reviewer->Fixer->Merge graph"
```

---

## Task 16: Runner (shared by API + scheduler)

**Files:**
- Create: `src/agentsys/graph/runner.py`
- Test: `tests/graph/test_runner.py`

The runner owns building agents from settings + clients, starting a run as a background task, and reading status from the checkpointer. To stay testable, it accepts an injected compiled-graph factory.

- [ ] **Step 1: Write the failing test**

`tests/graph/test_runner.py`:
```python
import pytest
from langgraph.checkpoint.memory import MemorySaver

from agentsys.graph.builder import build_graph
from agentsys.graph.runner import Runner
from agentsys.graph.state import WorkflowState


class StubAgent:
    def __init__(self, name):
        self.name = name

    async def run(self, state):
        return {self.name: {"note": "ok"}}


@pytest.mark.asyncio
async def test_start_run_and_get_run():
    agents = {n: StubAgent(n) for n in ["pm", "dev", "reviewer", "fixer"]}
    graph = build_graph(agents, checkpointer=MemorySaver())
    runner = Runner(graph=graph)

    run_id = await runner.start_run(board="github", item_id="42", wait=True)
    status = await runner.get_run(run_id)

    assert status["status"] == "completed"
    assert status["item_id"] == "42"


@pytest.mark.asyncio
async def test_get_run_unknown_returns_none():
    agents = {n: StubAgent(n) for n in ["pm", "dev", "reviewer", "fixer"]}
    graph = build_graph(agents, checkpointer=MemorySaver())
    runner = Runner(graph=graph)
    assert await runner.get_run("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_runner.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/graph/runner.py`**

```python
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

from agentsys.graph.state import WorkflowState
from agentsys.observability.logging import bind_run_context

logger = structlog.get_logger()


class Runner:
    """Starts graph runs as background tasks; reads status from the checkpointer."""

    def __init__(self, *, graph: Any, langfuse_handler: Any | None = None) -> None:
        self._graph = graph
        self._langfuse = langfuse_handler
        self._tasks: set[asyncio.Task[Any]] = set()

    def _config(self, thread_id: str) -> dict[str, Any]:
        cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        if self._langfuse is not None:
            cfg["callbacks"] = [self._langfuse]
        return cfg

    async def start_run(self, *, board: str, item_id: str, wait: bool = False) -> str:
        run_id = uuid.uuid4().hex
        bind_run_context(run_id=run_id, thread_id=run_id)
        initial = WorkflowState(run_id=run_id, board=board, item_id=item_id)

        async def _invoke() -> None:
            await self._graph.ainvoke(initial, config=self._config(run_id))

        if wait:
            await _invoke()
        else:
            task = asyncio.create_task(_invoke())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        logger.info("run_started", board=board, item_id=item_id)
        return run_id

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        snapshot = await self._graph.aget_state(self._config(run_id))
        if not snapshot or not snapshot.values:
            return None
        values = snapshot.values
        return values if isinstance(values, dict) else values.model_dump()
```

> **Note:** `aget_state` returns a `StateSnapshot`; `.values` holds the current
> state (a dict for compiled graphs). The `wait=True` path is what tests and the
> scheduler's synchronous needs use; the API uses `wait=False` for background
> execution.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/graph/test_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(graph): runner with background start and checkpointer status read"
```

---

## Task 17: Application wiring (build agents + graph from settings)

**Files:**
- Create: `src/agentsys/app_context.py`
- Test: `tests/test_app_context.py`

This is the composition root that builds real clients, agents, and the graph from `Settings`. It is async because it opens the checkpointer.

- [ ] **Step 1: Write the failing test**

`tests/test_app_context.py`:
```python
import pytest
from langgraph.checkpoint.memory import MemorySaver

from agentsys.app_context import build_agents
from agentsys.config.settings import Settings


def test_build_agents_returns_all_four():
    settings = Settings(github_token="t", anthropic_api_key="k")

    class FakeGitHub:
        async def get_issue(self, item_id): ...
        async def post_comment(self, item_id, body): ...

    class FakeModel:
        async def ainvoke(self, messages, **kwargs): ...

    agents = build_agents(settings, github=FakeGitHub(), pm_model=FakeModel())
    assert set(agents) == {"pm", "dev", "reviewer", "fixer"}
    assert agents["pm"].name == "pm"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app_context.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/app_context.py`**

```python
from __future__ import annotations

from typing import Any

from agentsys.agents.base import BaseAgent
from agentsys.agents.dev import DevAgent
from agentsys.agents.fixer import FixerAgent
from agentsys.agents.pm import PMAgent
from agentsys.agents.reviewer import ReviewerAgent
from agentsys.config.settings import Settings


def build_agents(
    settings: Settings,
    *,
    github: Any,
    pm_model: Any | None = None,
) -> dict[str, BaseAgent]:
    return {
        "pm": PMAgent(settings, github=github, model=pm_model),
        "dev": DevAgent(settings),
        "reviewer": ReviewerAgent(settings),
        "fixer": FixerAgent(settings),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: composition root building agents from settings"
```

---

## Task 18: FastAPI schemas + routes + app (lifespan)

**Files:**
- Create: `src/agentsys/api/__init__.py`
- Create: `src/agentsys/api/schemas.py`
- Create: `src/agentsys/api/routes.py`
- Create: `src/agentsys/api/app.py`
- Test: `tests/api/test_routes.py`

Routes depend on a `Runner` resolved from app state, so tests can inject a fake runner via dependency override.

- [ ] **Step 1: Write the failing test**

`tests/api/test_routes.py`:
```python
import pytest
from fastapi.testclient import TestClient

from agentsys.api.app import create_app
from agentsys.api.routes import get_runner


class FakeRunner:
    async def start_run(self, *, board, item_id, wait=False):
        return "run-123"

    async def get_run(self, run_id):
        if run_id == "run-123":
            return {"run_id": "run-123", "status": "completed", "item_id": "42"}
        return None


def test_post_run_returns_run_id_and_get_run_reads_status():
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: FakeRunner()
    client = TestClient(app)

    resp = client.post("/runs", json={"board": "github", "item_id": "42"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert run_id == "run-123"

    status = client.get(f"/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"


def test_get_unknown_run_returns_404():
    app = create_app()
    app.dependency_overrides[get_runner] = lambda: FakeRunner()
    client = TestClient(app)
    assert client.get("/runs/missing").status_code == 404


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_routes.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/api/schemas.py`**

```python
from __future__ import annotations

from pydantic import BaseModel


class RunRequest(BaseModel):
    board: str = "github"
    item_id: str


class RunCreated(BaseModel):
    run_id: str
```

- [ ] **Step 4: Implement `src/agentsys/api/routes.py`**

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agentsys.api.schemas import RunCreated, RunRequest

router = APIRouter()


def get_runner(request: Request) -> Any:
    return request.app.state.runner


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/runs", response_model=RunCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_run(body: RunRequest, runner: Any = Depends(get_runner)) -> RunCreated:
    run_id = await runner.start_run(board=body.board, item_id=body.item_id)
    return RunCreated(run_id=run_id)


@router.get("/runs/{run_id}")
async def read_run(run_id: str, runner: Any = Depends(get_runner)) -> dict[str, Any]:
    result = await runner.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result
```

- [ ] **Step 5: Implement `src/agentsys/api/app.py`**

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from agentsys.api.routes import router
from agentsys.app_context import build_agents
from agentsys.clients.github import GitHubClient
from agentsys.config.settings import Settings
from agentsys.graph.builder import build_graph
from agentsys.graph.checkpointer import checkpointer_from_dsn
from agentsys.graph.runner import Runner
from agentsys.observability.langfuse import make_langfuse_handler
from agentsys.observability.logging import configure_logging
from agentsys.scheduler.board_scan import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)

    async with httpx.AsyncClient(base_url="https://api.github.com") as http, \
        checkpointer_from_dsn(settings.postgres_dsn) as saver:
        await saver.setup()
        github = GitHubClient(
            token=settings.github_token, repo=settings.github_repo, http=http
        )
        agents = build_agents(settings, github=github)
        graph = build_graph(agents, checkpointer=saver)
        runner = Runner(graph=graph, langfuse_handler=make_langfuse_handler(settings))
        app.state.runner = runner

        scheduler = start_scheduler(settings, runner)
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="agentsys", lifespan=lifespan)
    app.state.settings = settings or Settings()  # reads GITHUB_REPO etc. from env
    app.include_router(router)
    return app
```

> **Note:** `create_app()` builds the app object without running lifespan; the
> `TestClient` in the test overrides `get_runner`, so lifespan's real Postgres/HTTP
> wiring is not exercised in unit tests. Integration against real services happens
> via docker-compose (Task 21 / DoD).

- [ ] **Step 6: Create `src/agentsys/api/__init__.py`** (empty) and `tests/api/__init__.py` (empty)

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_routes.py -v`
Expected: PASS (2 tests)

> If import of `start_scheduler` fails because Task 19 is not yet done, implement
> Task 19 first or temporarily stub `start_scheduler`. Recommended: do Task 19
> before running this step. (Plan ordering note — see Task 19.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(api): FastAPI app, /runs routes, lifespan wiring"
```

---

## Task 19: APScheduler board-scan job

> **Ordering:** implement this task's module before running Task 18 Step 7, since
> `api/app.py` imports `start_scheduler`. It is listed after Task 18 only because
> the API defines why the scheduler exists; create `scheduler/board_scan.py`
> before executing Task 18's test step.

**Files:**
- Create: `src/agentsys/scheduler/__init__.py`
- Create: `src/agentsys/scheduler/board_scan.py`
- Test: `tests/scheduler/test_board_scan.py`

- [ ] **Step 1: Write the failing test**

`tests/scheduler/test_board_scan.py`:
```python
import pytest

from agentsys.config.settings import Settings
from agentsys.scheduler.board_scan import scan_board, start_scheduler


class FakeRunner:
    def __init__(self):
        self.started = []

    async def start_run(self, *, board, item_id, wait=False):
        self.started.append((board, item_id))
        return "r"


@pytest.mark.asyncio
async def test_scan_board_triggers_runner_for_each_item():
    runner = FakeRunner()

    async def fake_discover():
        return ["1", "2"]

    await scan_board(runner, discover=fake_discover)
    assert runner.started == [("github", "1"), ("github", "2")]


def test_start_scheduler_disabled_returns_none():
    settings = Settings(github_token="t", anthropic_api_key="k", board_scan_enabled=False)
    assert start_scheduler(settings, FakeRunner()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_board_scan.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/agentsys/scheduler/board_scan.py`**

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agentsys.config.settings import Settings

logger = structlog.get_logger()


async def _default_discover() -> list[str]:
    # STUB. TODO: query the board for new/changed items since the last scan.
    return []


async def scan_board(
    runner: Any,
    *,
    discover: Callable[[], Awaitable[list[str]]] = _default_discover,
) -> None:
    item_ids = await discover()
    for item_id in item_ids:
        await runner.start_run(board="github", item_id=item_id)
        logger.info("scheduled_run_triggered", item_id=item_id)


def start_scheduler(settings: Settings, runner: Any) -> AsyncIOScheduler | None:
    if not settings.board_scan_enabled:
        return None
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scan_board,
        "interval",
        seconds=settings.board_scan_interval_seconds,
        args=[runner],
    )
    scheduler.start()
    return scheduler
```

- [ ] **Step 4: Create `src/agentsys/scheduler/__init__.py`** (empty) and `tests/scheduler/__init__.py` (empty)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_board_scan.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(scheduler): APScheduler board-scan job calling the runner"
```

---

## Task 20: Shared test fixtures (conftest)

**Files:**
- Create: `tests/conftest.py`
- Modify: none

This consolidates the fake model / fake GitHub fixtures used across agent tests so future tests reuse them. (Earlier tasks defined fakes inline; this is the DRY consolidation step — leave existing inline fakes or refactor to use these fixtures.)

- [ ] **Step 1: Implement `tests/conftest.py`**

```python
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from agentsys.clients.github import Issue
from agentsys.config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(github_token="test-token", anthropic_api_key="test-key")


@pytest.fixture
def fake_github():
    class FakeGitHub:
        def __init__(self) -> None:
            self.posted: tuple[str, str] | None = None

        async def get_issue(self, item_id: str) -> Issue:
            return Issue(number=int(item_id), title="Title", body="Body")

        async def post_comment(self, item_id: str, body: str) -> str:
            self.posted = (item_id, body)
            return "https://gh/comment/1"

    return FakeGitHub()


@pytest.fixture
def fake_model():
    class FakeModel:
        def bind_tools(self, tools):  # reusable by tool-calling agents
            return self

        async def ainvoke(self, messages, **kwargs) -> AIMessage:
            return AIMessage(content="## Spec\n- item")

    return FakeModel()
```

- [ ] **Step 2: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (all prior tests still green; conftest adds fixtures without breaking anything)

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: shared conftest fixtures for settings, github, model"
```

---

## Task 21: docker-compose, README, end-to-end manual check

**Files:**
- Create: `docker-compose.yml`
- Create: `README.md`

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: agentsys
      POSTGRES_PASSWORD: agentsys
      POSTGRES_DB: agentsys
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agentsys"]
      interval: 5s
      timeout: 5s
      retries: 5

  chroma:
    image: chromadb/chroma:latest
    ports:
      - "8000:8000"
    volumes:
      - chroma-data:/chroma/chroma

volumes:
  postgres-data:
  chroma-data:
```

- [ ] **Step 2: Create `README.md`**

````markdown
# agentsys — AI-Driven PM + Engineering Multi-Agent Boilerplate

A LangGraph-orchestrated multi-agent skeleton (PM → Dev → Reviewer → Fixer → Merge).
The full graph runs end-to-end; only the **PM agent** is real (reads a GitHub Issue,
generates a technical spec, posts it back as a comment). Dev/Reviewer/Fixer are
pass-through stubs. See `docs/superpowers/specs/` for the design.

## Quickstart

```bash
git clone <repo> && cd agentsys
cp .env.example .env          # set GITHUB_TOKEN, GITHUB_REPO (owner/name), ANTHROPIC_API_KEY
docker compose up -d          # Postgres + Chroma
uv sync --all-extras --dev
uv run uvicorn agentsys.api.app:create_app --factory --host 0.0.0.0 --port 8080
```

Trigger a run against a GitHub issue in the repo set by `GITHUB_REPO`:

```bash
curl -X POST localhost:8080/runs -H 'content-type: application/json' \
  -d '{"board":"github","item_id":"42"}'
# -> {"run_id":"..."}

curl localhost:8080/runs/<run_id>
# -> {"status":"completed", "pm": {"spec":"...", "comment_url":"..."}, ...}
```

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
```

## Extending

- **Real agents:** fill in `agents/{dev,reviewer,fixer}.py` and their toolkits.
- **More tools:** add a `clients/<x>.py`, wrap it in `toolkits/<x>.py` with `@tool`,
  bind it in the agent's `get_tools()`.
- **More boards:** add a client + toolkit; the graph is board-agnostic.
- **Durable queue:** replace the background task in `graph/runner.py` with an enqueue
  to a worker; un-stub `clients/redis.py`.
- **Metrics/log shipping:** Prometheus and ELK are documented seams (not wired).
````

- [ ] **Step 3: Verify the full suite + lint + types**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```
Expected: all green.

- [ ] **Step 4: Manual end-to-end (requires real keys + docker)**

Run:
```bash
docker compose up -d
# wait for healthy postgres/chroma
uv run uvicorn agentsys.api.app:create_app --factory --port 8080 &
curl -X POST localhost:8080/runs -H 'content-type: application/json' \
  -d '{"board":"github","item_id":"<real-issue-number>"}'
# then GET /runs/<run_id> until status == "completed"
```
Expected: a comment containing a generated spec appears on the GitHub issue; `GET`
shows `status: completed` with `pm.spec` and `pm.comment_url` populated.

> This manual step validates the Definition of Done (spec §12). It is not part of CI
> (no live credentials in CI).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: docker-compose (postgres+chroma), README, e2e instructions"
```

---

## Self-Review (completed)

**Spec coverage:**
- §2 tech decisions → Tasks 1–21 collectively. Per-agent overrides (Task 2/5/12), provider-agnostic factory (Task 5), namespaced state (Task 10), Postgres checkpointer (Task 11), 3-layer tools (Tasks 6–9), background execution (Task 16/18), APScheduler (Task 19), structlog (Task 3), Langfuse (Task 4), Chroma+Postgres (Task 7), stubs (Tasks 6/9/12), uv/ruff/mypy/pytest/CI (Task 1), docker-compose (Task 21). ✓
- §4 agent abstraction → Task 12. ✓  §5 orchestration/state → Tasks 10/11/14/15/16. ✓
- §6 tools (3-layer, LLM-chosen) → Tasks 6/8/9; the PM agent (Task 13) exercises the
  real `bind_tools` tool-calling loop so the `@tool` path is proven, not just defined. ✓
  §7 data layer → Tasks 6/7. ✓  §8 entry/exec/sched → Tasks 16/18/19; `GET /health`
  and `GITHUB_REPO` setting added for production-shaped wiring. ✓
- §9 config/observability/errors → Tasks 2/3/4/14. ✓  §10 testing → every task is TDD + Task 20. ✓
- §11 toolchain → Task 1. ✓  §12 DoD → Task 21 Step 4. ✓

**Placeholder scan:** No "TBD/implement later" in requirement steps. The only
`NotImplementedError`/`TODO` strings are intentional **stub behavior** the tests
assert against (Tasks 6, 9, 12, 19). ✓

**Type consistency:** `Settings.model_for` (Task 2) used by `BaseAgent.get_model`
(Task 12) and `get_chat_model(LLMSettings, api_key=...)` (Task 5) — signatures match.
`Runner.start_run(board=, item_id=, wait=)` / `get_run(run_id)` (Task 16) match calls
in routes (Task 18) and scheduler (Task 19). `make_node(agent)` (Task 14) used by
`build_graph` (Task 15). `WorkflowState` field names (Task 10) match node return dicts
(Tasks 12/13) and merge logic (Task 15). ✓

**Ordering note:** Task 19 (`scheduler/board_scan.py`) must be created before Task 18
Step 7 runs, because `api/app.py` imports `start_scheduler`. Flagged inline in both tasks.
