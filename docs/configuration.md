# Configuration reference (env vars + models)

ASH has **two config layers**:

1. **`Settings`** — environment variables / `.env` (secrets, LLM, DB, admin). This doc.
2. **`projects/<name>.yaml`** — per-engagement repo topology, autonomy, budget (see that file).

Env vars are read by `pydantic-settings`. Field names map to **UPPERCASE** env vars; nested
(per-agent) fields use a **double underscore** `__`.

---

## All environment variables

| Env var | Default | What it does |
|---|---|---|
| **LLM — global default** | | |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai`. **`openai` = any OpenAI-compatible host** (LiteLLM / Ollama / vLLM / OpenAI). |
| `LLM_MODEL` | `claude-sonnet-4-6` | The model id **as your provider/gateway names it** (see below). |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature. |
| `LLM_MAX_TOKENS` | `8192` | Max output tokens. |
| `LLM_BASE_URL` | *(unset)* | Gateway URL for `openai`, e.g. `https://your-litellm/v1`. Leave unset for real OpenAI/Anthropic. |
| **LLM — credentials** | | |
| `ANTHROPIC_API_KEY` | `""` | Used when the **effective provider is `anthropic`**. |
| `OPENAI_API_KEY` | `""` | Used when the **effective provider is `openai`** — including LiteLLM/gateway keys. |
| **LLM — per-agent overrides** (optional; blank → use the global value) | | |
| `AGENT_PM__MODEL` / `AGENT_PM__PROVIDER` / `AGENT_PM__TEMPERATURE` / `AGENT_PM__MAX_TOKENS` | *(unset)* | Override for the **PM** agent. |
| `AGENT_RESEARCH__*` | *(unset)* | Override for **Research**. |
| `AGENT_CODING__*` | *(unset)* | Override for **Coding**. |
| `AGENT_REVIEWER__*` | *(unset)* | Override for **Reviewer**. |
| `AGENT_FIXER__*` | *(unset)* | Override for **Fixer**. |
| **Database** | | |
| `POSTGRES_DSN` | `postgresql://ash:ash@localhost:5432/ash` | Postgres for the LangGraph checkpointer **and** app tables. In docker-compose this is overridden to the `postgres` service. |
| **Integrations / admin** | | |
| `SECRET_KEY` | `""` | Fernet key — **encrypts integration tokens at rest**. Required to add/use integrations. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `ADMIN_USER` | `admin` | Bootstrap admin login for `/admin`. |
| `ADMIN_PASSWORD` | `admin` | Bootstrap admin password (override it!). DB users (`just create-admin`) take precedence. |
| **GitHub (legacy / fallback source)** | | |
| `GITHUB_TOKEN` | `""` | Used by the legacy GitHub intake fallback (when a run has no `integration_id`). Real integrations carry their own token. |
| **Build agents** | | |
| `LOCAL_REPO_PATH` | *(unset)* | Path to a local clone of the work-target repo; enables Research/Coding. Without it they skip gracefully. |
| **Misc** | | |
| `LOG_LEVEL` | `INFO` | Log level. |
| `ASH_ROOT` | *(auto)* | Override the detected repo root (where `projects/` lives). Rarely needed. |

> `extra="ignore"`: unknown env vars are ignored. So a typo like `LLM_PROVIDR` is **silently
> dropped** and the default is used — double-check spelling if a setting "doesn't take effect".

---

## How the model is chosen (per agent)

For each agent (`pm`, `research`, `coding`, `reviewer`, `fixer`):

```
provider = AGENT_<NAME>__PROVIDER or LLM_PROVIDER
model    = AGENT_<NAME>__MODEL    or LLM_MODEL
api_key  = ANTHROPIC_API_KEY  (if provider == anthropic)
           OPENAI_API_KEY     (if provider == openai)
base_url = LLM_BASE_URL  (applies to both providers)
```

So by default **all agents use `LLM_MODEL`**; set `AGENT_*__MODEL` only to diverge.

**Important:** `LLM_MODEL` must be a model id your provider/gateway actually serves *and your key is
allowed to use*. The model name is **not** validated by ASH — the provider returns the error. Common
ones you saw:

- `key only access models=['general'] … tried claude-sonnet-4-6` → your LiteLLM key is scoped to the
  model alias `general`. Set `LLM_MODEL=general` (and any `AGENT_*__MODEL` likewise).
- `Invalid proxy server token` / `Missing credentials` → `OPENAI_API_KEY` wrong/unset.
- `Could not resolve authentication method` → provider is `anthropic` but `ANTHROPIC_API_KEY` unset
  (often because `LLM_PROVIDER` wasn't actually applied — check spelling).

---

## Working `.env` examples

### A) LiteLLM (or any OpenAI-compatible) gateway

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://your-litellm-host/v1
OPENAI_API_KEY=sk-...            # your LiteLLM virtual key
LLM_MODEL=general                # a model your key is allowed to access (LiteLLM alias)

POSTGRES_DSN=postgresql://ash:ash@localhost:5432/ash
SECRET_KEY=<fernet-key>
ADMIN_USER=admin
ADMIN_PASSWORD=change-me
```

### B) Native Anthropic

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6
# LLM_BASE_URL unset
```

### C) Different model per agent (e.g. cheap research, strong PM)

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://your-litellm-host/v1
OPENAI_API_KEY=sk-...
LLM_MODEL=general                # global default

AGENT_PM__MODEL=gpt-4o           # only if your key can access these aliases
AGENT_CODING__MODEL=qwen-coder
```

---

## Notes for docker-compose

- The `api` service loads `.env` via `env_file`, and **overrides `POSTGRES_DSN`** to reach the
  `postgres` service by name — you don't set that one for docker.
- After editing `.env`, **restart** so it's reloaded: `docker compose restart api`.
- After changing **code**, rebuild: `docker compose up -d --build api`.

---

## Quick checklist when the LLM step errors

1. `LLM_PROVIDER` spelled exactly (`openai` / `anthropic`)?
2. The matching key set? (`OPENAI_API_KEY` for openai, `ANTHROPIC_API_KEY` for anthropic)
3. `LLM_BASE_URL` set for a gateway (and unset for real OpenAI/Anthropic)?
4. `LLM_MODEL` is a model your key is **allowed** to use?
5. Restarted the container after editing `.env`?
