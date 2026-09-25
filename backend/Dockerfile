# One image for api, worker and migrate (different commands). Multi-stage, slim, non-root.
# Plain Dockerfile syntax (no BuildKit-only features), so it builds with or without buildx.

FROM ghcr.io/astral-sh/uv:0.11.7 AS uv

FROM python:3.12-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-cache

FROM python:3.12-slim-trixie AS runtime
# Pull in security fixes for the base OS packages, then drop apt metadata.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config ./config
COPY --chown=app:app prompts ./prompts
# Golden ingest cases: the offline fake brain replays their recorded model outputs.
COPY --chown=app:app evals ./evals
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
ARG APP_VERSION=dev
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RESOURCES_DIR=/app \
    APP_VERSION=${APP_VERSION}
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=4s --start-period=20s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["uvicorn", "--factory", "secondmind.api.main:create", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
