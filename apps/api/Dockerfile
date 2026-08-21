# syntax=docker/dockerfile:1
#
# Two targets:
#   dev   — dev dependencies + uvicorn --reload, for docker compose
#   prod  — runtime dependencies only, non-root, no build tooling in the image
#
# Build from the repo root:
#   docker build --target prod --build-arg GIT_SHA=$(git rev-parse HEAD) apps/api

# --------------------------------------------------------------------------
# Base — uv's official image, which is python:3.12-slim-bookworm plus uv.
# --------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# --------------------------------------------------------------------------
# Dependency layers — cached on the lockfile alone, so editing source code
# never reinstalls the world.
# --------------------------------------------------------------------------
FROM base AS deps-runtime
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

FROM base AS deps-dev
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# --------------------------------------------------------------------------
# dev — hot reload. Compose bind-mounts ./apps/api/src over /app/src.
# --------------------------------------------------------------------------
FROM base AS dev

ARG GIT_SHA=unknown
ENV BUILD_ENV=dev \
    GIT_SHA=${GIT_SHA}

# Before the dependency layer, so editing a script or a source file does not
# reinstall a driver from the network.
COPY docker/install-odbc.sh /tmp/install-odbc.sh
RUN sh /tmp/install-odbc.sh && rm /tmp/install-odbc.sh

COPY --from=deps-dev /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

# **The image declares the two directories it writes to** (WP11.2b). The API
# writes encrypted data-source credentials to one and stored query results to the
# other, and compose mounts something over both. Creating them here, before the
# chown, is what makes the mount usable: Docker seeds a *named volume* from the
# image's content and ownership, but only when the path exists in the image —
# otherwise the volume is created empty and owned by **root**, and `app` cannot
# write to it. That is the failure the compose smoke hit on its first CI run,
# `PermissionError: '/app/ops/.secrets/secrets.json.8.tmp'`.
#
# It does nothing for a *bind* mount, which never inherits image ownership. A
# host directory mounted here has to be writable by uid 1001 by its own arrangement.
RUN mkdir -p /app/ops/.secrets /app/ops/artifacts \
    && groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app \
    && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "dataagent.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --------------------------------------------------------------------------
# Builder for prod — installs the project itself against runtime deps only.
# --------------------------------------------------------------------------
FROM base AS build
COPY --from=deps-runtime /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# --------------------------------------------------------------------------
# prod — runtime only. No uv, no compilers, no dev dependencies, non-root.
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS prod

ARG GIT_SHA=unknown
ENV BUILD_ENV=prod \
    GIT_SHA=${GIT_SHA} \
    ENV=prod \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --no-create-home app

# The same driver install as the dev target, from the same script. Runs before
# the application layers so it is cached across source changes.
COPY docker/install-odbc.sh /tmp/install-odbc.sh
RUN sh /tmp/install-odbc.sh && rm /tmp/install-odbc.sh

WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --from=build --chown=app:app /app/src /app/src

# The development token issuer is physically absent from this image. Settings
# already refuse AUTH_MODE=dev in a prod build; removing the module means even a
# process that somehow got past that check has nothing to import and mint with.
RUN rm -f /app/src/dataagent/auth/dev_issuer.py \
    && test ! -f /app/src/dataagent/auth/dev_issuer.py

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "dataagent.main:app", "--host", "0.0.0.0", "--port", "8000"]
