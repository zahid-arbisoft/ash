# ASH (Agentic Software House) — command runner. Run `just` to list recipes.

set dotenv-load := true

py := ".venv/bin/python"
pip := ".venv/bin/pip"
ruff := ".venv/bin/ruff"
mypy := ".venv/bin/mypy"
run := py + " -m ash.cli"

# default target: show available recipes
default:
    @just --list

# create the virtualenv (py>=3.12) and install the engine (editable) + dev tools
setup:
    python3 -m venv .venv
    {{pip}} install --upgrade pip
    {{pip}} install -e ".[dev]"
    @echo "done. copy .env.example -> .env, add credentials, then 'docker compose up postgres'."

install:
    {{pip}} install -e ".[dev]"

# ── engine (CLI) ──
# list open issues from a project's source repo: just list plane
list project="plane" limit="20":
    {{run}} list --project {{project}} --limit {{limit}}

# run the full graph once for one issue (in-memory checkpointer): just run plane 9213
run project issue:
    {{run}} run --project {{project}} --issue {{issue}}

# create (or reset) an admin-portal user; prompts for the password: just create-admin alice
create-admin user:
    {{run}} create-admin --username {{user}}

# ── API ──
# run the FastAPI app (needs Postgres up): http://127.0.0.1:8000/docs
serve:
    {{py}} -m uvicorn ash.api.app:app --reload

# ── data services ──
db-up:
    docker compose up -d postgres

db-down:
    docker compose down

# ── quality gates ──
lint:
    {{ruff}} check .

fmt:
    {{ruff}} format .
    {{ruff}} check --fix .

typecheck:
    {{mypy}}

test:
    {{py}} -m pytest

# everything CI runs
check: lint typecheck test

# ── docker ──
docker-up:
    docker compose up --build

docker-down:
    docker compose down

docker-secret-key:
    docker compose exec api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ── misc ──
clean:
    find runtime -mindepth 1 ! -name .gitkeep -delete
    @echo "runtime/ cleaned"
