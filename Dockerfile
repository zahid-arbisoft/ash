# ASH — Agentic Software House engine (FastAPI + LangGraph).
# NOTE: agent runs that push code need git credentials (gh/SSH) — not wired into the image yet.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is required by GitPython (worktree ops)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Dependency layer — only re-runs when pyproject.toml changes (NOT on code edits).
#    A throwaway stub package lets pip build metadata + install all third-party deps
#    without the real source, so this expensive layer stays cached across code changes.
COPY pyproject.toml ./
RUN mkdir -p src/ash && touch src/ash/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf src build ./*.egg-info src/*.egg-info

# 2) Editable install of the real package — fast, no downloads (--no-deps), reuses the
#    cached deps layer above. Only this cheap step re-runs when source changes.
COPY . .
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "ash.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
