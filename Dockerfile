# ASH — Agentic Software House engine (FastAPI + LangGraph).
# NOTE: agent runs that push code need git credentials (gh/SSH) — not wired into the image yet.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# git is required by GitPython (worktree ops)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better layer caching)
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

# Copy the rest (projects/, skills/, etc.)
COPY . .

EXPOSE 8000

CMD ["uvicorn", "ash.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
