# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
WORKDIR /app

# Dependencies first, in their own layer: they are re-installed only when pyproject.toml changes,
# not on every code change. The cache mount keeps downloaded wheels between builds, so a build
# that fails half-way on a slow network resumes instead of starting over.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir app && touch app/__init__.py && pip install . && rm -rf app

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

EXPOSE 8000
# --no-server-header: uvicorn would otherwise stamp "server: uvicorn" on every response, which
# is a technology fingerprint (docs/SECURITY_HEADERS.md).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header"]
