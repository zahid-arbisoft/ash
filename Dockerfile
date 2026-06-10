# ASH control plane (Django) + engine. Runs the web/admin + management commands.
# NOTE: agent runs that push code need git credentials (gh/SSH) — not wired into the image yet;
# this image is for the control plane and engine logic. See README.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.dev

# git is required by GitPython (worktree ops)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (better layer caching): copy only what the build needs
COPY pyproject.toml ./
COPY engine ./engine
RUN pip install --no-cache-dir -e ".[server]"

# Copy the rest of the monorepo
COPY . .

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && python manage.py runserver 0.0.0.0:8000"]
