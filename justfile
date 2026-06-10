# ASH (Agentic Software House) — command runner. Run `just` to list recipes.

set dotenv-load := true

py := ".venv/bin/python"
pip := ".venv/bin/pip"
ruff := ".venv/bin/ruff"
# engine is editable-installed, so no PYTHONPATH needed
run := py + " -m ash.main"

# default target: show available recipes
default:
    @just --list

# create the virtualenv and install the engine (editable) + control plane + dev tools
setup:
    python3 -m venv .venv
    {{pip}} install --upgrade pip
    {{pip}} install -e ".[server,dev]"
    {{py}} manage.py migrate
    @echo "done. copy .env.example -> .env and add your LLM credentials."

# install/update dependencies into the existing venv
install:
    {{pip}} install -e ".[server,dev]"

# ── engine (CLI) ──
# list open issues from a project's source repo: just list plane
list project="plane" limit="20":
    {{run}} list --project {{project}} --limit {{limit}}

# build a spec for one issue (-> Board): just spec plane 9213
spec project issue:
    {{run}} spec --project {{project}} --issue {{issue}}

# full build-team flow (CLI, no DB): spec->Board, research, code -> fork PR
build project issue:
    {{run}} build --project {{project}} --issue {{issue}}

# ── control plane (Django) ──
# build via the control plane (persists a Run row): just house-build plane 9213
house-build project issue:
    {{py}} manage.py build --project {{project}} --issue {{issue}}

migrate:
    {{py}} manage.py migrate

makemigrations:
    {{py}} manage.py makemigrations

superuser:
    {{py}} manage.py createsuperuser

# run the control-plane admin UI at http://127.0.0.1:8000
serve:
    {{py}} manage.py runserver

# ── quality ──
lint:
    {{ruff}} check .

fmt:
    {{ruff}} format .
    {{ruff}} check --fix .

test:
    {{py}} -m pytest

check:
    {{py}} manage.py check

# ── docker ──
docker-build:
    docker compose build

docker-up:
    docker compose up

docker-down:
    docker compose down

# ── misc ──
# quick env check: provider/model/key
doctor:
    @{{py}} -c "from ash.config import load_llm_settings as s; c=s(); print('provider:', c.provider); print('base_url:', c.base_url or '(default)'); print('pm model:', c.model_for('pm')); print('api key:', 'set' if c.api_key else 'MISSING')" 2>/dev/null || echo "run 'just setup' first"

# remove generated runtime state (specs, db, worktrees)
clean:
    find runtime -mindepth 1 ! -name .gitkeep -delete
    @echo "runtime/ cleaned"
