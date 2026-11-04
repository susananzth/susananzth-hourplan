# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.14.10-alpine3.24
ARG NODE_IMAGE=node:24.22.2-alpine3.24
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7

# ---------------------------------------------------------------- uv binary
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- CSS and browser libraries
FROM ${NODE_IMAGE} AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY scripts/copy-vendor.mjs scripts/
COPY assets assets
COPY src/hourplan src/hourplan
RUN npm run build

# ---------------------------------------------------------------- Python base
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# ---------------------------------------------------------------- production dependencies
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- development and CI tools
FROM base AS dev
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
ENV HOME=/tmp \
    HOURPLAN_ENV=development \
    FLASK_APP=hourplan
COPY . .
CMD ["flask", "run", "--host", "0.0.0.0", "--port", "8000", "--debug"]

# ---------------------------------------------------------------- production image
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH \
    HOURPLAN_ENV=production \
    FLASK_APP=hourplan
# pip is not needed at runtime and its vendored libraries carry their own CVEs.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/bin/pip* \
    && addgroup -S -g 10001 app && adduser -S -D -H -u 10001 -G app app
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations migrations
COPY src src
COPY --from=assets /build/src/hourplan/static/css src/hourplan/static/css
COPY --from=assets /build/src/hourplan/static/vendor src/hourplan/static/vendor
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8000/healthz >/dev/null || exit 1
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "hourplan:create_app()"]
