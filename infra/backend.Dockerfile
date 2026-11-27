# Backend image. Build context: backend/ (docker compose and render.yaml).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# The app user owns /app from the start: files are copied with --chown instead of a final
# `chown -R`, which would duplicate every file in an extra layer (~2x image size).
RUN useradd --system --uid 10001 app && mkdir /app && chown app /app
WORKDIR /app
USER app

COPY --chown=app pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app . .

# Render sets PORT (10000 by default); docker compose uses 8000.
EXPOSE 8000
# Migrations run on start so a fresh database always has the current schema.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
